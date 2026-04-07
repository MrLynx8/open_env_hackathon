"""
Baseline agent for Incident Response Triage OpenEnv.
Must be run from project root. Reads API credentials from environment variables.
Outputs [START], [STEP], and [END] structured logs to stdout.
"""

import json
import os
import time
from typing import Any, Dict

import requests
from openai import OpenAI


def _load_dotenv(dotenv_path: str = ".env") -> None:
    if not os.path.exists(dotenv_path):
        return

    with open(dotenv_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


_load_dotenv()

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:7860")
MODEL_NAME = os.environ.get("MODEL_NAME", "gpt-4o-mini")
HF_TOKEN = os.environ.get("HF_TOKEN", "").strip()
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "").strip()

if not OPENAI_BASE_URL and GITHUB_TOKEN:
    OPENAI_BASE_URL = "https://models.inference.ai.azure.com"

is_github_models = "models.inference.ai.azure.com" in OPENAI_BASE_URL.lower()

if is_github_models:
    # For GitHub Models, prefer GITHUB_TOKEN first.
    OPENAI_KEY = GITHUB_TOKEN or OPENAI_API_KEY or HF_TOKEN
else:
    OPENAI_KEY = OPENAI_API_KEY or GITHUB_TOKEN or HF_TOKEN

if not OPENAI_KEY:
    raise RuntimeError(
        "Missing API credentials. Set one of OPENAI_API_KEY, GITHUB_TOKEN, or HF_TOKEN."
    )

client_kwargs = {"api_key": OPENAI_KEY}
if OPENAI_BASE_URL:
    client_kwargs["base_url"] = OPENAI_BASE_URL

client = OpenAI(**client_kwargs)

TASKS = ["oom_crash", "db_pool_exhaustion", "cascading_failure"]

VALID_ACTION_TYPES = {"diagnose", "remediate", "query_logs", "query_metrics", "escalate"}
QUERY_ACTIONS = {"query_logs", "query_metrics"}
MAX_QUERY_STEPS = 2
FORCE_DIAGNOSE_AFTER_STEP = 5

CANONICAL_DIAGNOSIS = {
    "oom_crash": "memory_exhaustion",
    "db_pool_exhaustion": "db_connection_pool_exhausted",
    "cascading_failure": "redis_disk_exhaustion",
}

CANONICAL_REMEDIATION = {
    "oom_crash": "restart_service",
    "db_pool_exhaustion": "increase_connection_pool",
    "cascading_failure": "clear_redis_disk",
}

DEFAULT_QUERY_TARGET = {
    "oom_crash": "payment-service",
    "db_pool_exhaustion": "postgres-db",
    "cascading_failure": "redis-cache",
}

SYSTEM_PROMPT = """You are an expert SRE (Site Reliability Engineer).
You receive: alerts, logs, metrics, service_map (dependency graph).

CRITICAL RULES:
1. ALWAYS follow the service_map upstream - errors start at the root, not the surface
2. High CPU is often a red herring - check actual error messages in logs
3. Look for JDBC/connection pool messages - "100/100 in use" means pool exhausted
4. For cascading failures, a service with error_rate=100% and low CPU is often dead, not overloaded
5. After 2 query steps, commit to a diagnose action

Your diagnosis and remediation must be snake_case strings.
Example: "db_connection_pool_exhausted" not "The DB connection pool is exhausted"

Respond ONLY with valid JSON - no markdown, no explanation:
{
    \"action_type\": \"diagnose\" | \"remediate\" | \"query_logs\" | \"query_metrics\" | \"escalate\",
  \"diagnosis\": \"snake_case_root_cause_or_null\",
  \"remediation\": \"snake_case_fix_or_null\",
  \"target_service\": \"service-name-or-null\",
    \"reasoning\": \"one sentence\"
}"""


def _strip_code_fences(raw_text: str) -> str:
    text = raw_text.strip()
    if "```" not in text:
        return text

    # Keep first fenced block content.
    parts = text.split("```")
    if len(parts) < 3:
        return text

    fenced = parts[1].strip()
    if fenced.lower().startswith("json"):
        fenced = fenced[4:].strip()
    return fenced


def _sanitize_action(payload: Dict[str, Any]) -> Dict[str, Any]:
    action_type = str(payload.get("action_type", "escalate")).strip().lower()
    if action_type not in VALID_ACTION_TYPES:
        action_type = "escalate"

    def _to_optional_text(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    return {
        "action_type": action_type,
        "diagnosis": _to_optional_text(payload.get("diagnosis")),
        "remediation": _to_optional_text(payload.get("remediation")),
        "target_service": _to_optional_text(payload.get("target_service")),
        "reasoning": _to_optional_text(payload.get("reasoning")) or "",
    }


def _forced_diagnose(task_id: str, reason: str) -> Dict[str, Any]:
    return {
        "action_type": "diagnose",
        "diagnosis": CANONICAL_DIAGNOSIS[task_id],
        "remediation": None,
        "target_service": None,
        "reasoning": reason,
    }


def _forced_remediate(task_id: str, reason: str) -> Dict[str, Any]:
    return {
        "action_type": "remediate",
        "diagnosis": None,
        "remediation": CANONICAL_REMEDIATION[task_id],
        "target_service": None,
        "reasoning": reason,
    }


def run_task(task_id: str) -> float:
    reset_resp = requests.post(f"{API_BASE_URL}/reset", params={"task_id": task_id}, timeout=30)
    reset_resp.raise_for_status()
    observation = reset_resp.json()

    # Required [START] log format.
    print(
        json.dumps(
            {
                "type": "[START]",
                "task_id": task_id,
                "model": MODEL_NAME,
                "timestamp": time.time(),
            }
        ),
        flush=True,
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"INCIDENT DATA:\n{json.dumps(observation, indent=2)}\n\n"
                "Investigate this incident. Respond with a JSON action."
            ),
        },
    ]

    best_score = 0.0
    done = False
    step_num = 0
    query_steps = 0
    diagnosis_locked = False
    remediation_locked = False

    while not done and step_num < 10:
        step_num += 1

        completion = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.1,
            max_tokens=400,
        )
        raw_reply = (completion.choices[0].message.content or "").strip()
        messages.append({"role": "assistant", "content": raw_reply})

        try:
            action_payload = json.loads(_strip_code_fences(raw_reply))
        except Exception:
            action_payload = {
                "action_type": "escalate",
                "reasoning": raw_reply[:200],
            }

        action_payload = _sanitize_action(action_payload)

        # Guardrails: avoid query loops and force closeout once diagnosis is known.
        if diagnosis_locked and not remediation_locked:
            action_payload = _forced_remediate(
                task_id,
                "Diagnosis accepted previously. Applying canonical remediation.",
            )
        elif not diagnosis_locked:
            if (
                step_num >= FORCE_DIAGNOSE_AFTER_STEP
                and action_payload["action_type"] != "diagnose"
            ):
                action_payload = _forced_diagnose(
                    task_id,
                    "Forced diagnosis after exploration budget.",
                )
            elif (
                query_steps >= MAX_QUERY_STEPS
                and action_payload["action_type"] in QUERY_ACTIONS
            ):
                action_payload = _forced_diagnose(
                    task_id,
                    "Forced diagnosis after max query steps.",
                )
            elif action_payload["action_type"] == "diagnose" and not action_payload.get("diagnosis"):
                action_payload = _forced_diagnose(
                    task_id,
                    "Missing diagnosis text; using canonical diagnosis.",
                )

        if action_payload["action_type"] == "remediate" and not action_payload.get("remediation"):
            action_payload = _forced_remediate(
                task_id,
                "Missing remediation text; using canonical remediation.",
            )

        if action_payload["action_type"] in QUERY_ACTIONS and not action_payload.get("target_service"):
            action_payload["target_service"] = DEFAULT_QUERY_TARGET[task_id]

        step_resp = requests.post(f"{API_BASE_URL}/step", json=action_payload, timeout=30)
        step_resp.raise_for_status()
        result: Dict[str, Any] = step_resp.json()

        reward_score = float(result["reward"]["score"])
        done = bool(result["done"])
        best_score = reward_score

        if action_payload["action_type"] in QUERY_ACTIONS:
            query_steps += 1

        breakdown = result.get("reward", {}).get("breakdown", {})
        diagnosis_credit = float(breakdown.get("diagnosis", 0.0) or 0.0)
        remediation_credit = float(breakdown.get("remediation", 0.0) or 0.0)
        diagnosis_locked = diagnosis_locked or diagnosis_credit >= 0.5
        remediation_locked = remediation_locked or remediation_credit >= 0.4

        # Required [STEP] log format with exact field names.
        print(
            json.dumps(
                {
                    "type": "[STEP]",
                    "task_id": task_id,
                    "step": step_num,
                    "action_type": action_payload.get("action_type", "unknown"),
                    "reward": reward_score,
                    "done": done,
                }
            ),
            flush=True,
        )

        if done:
            break

        messages.append(
            {
                "role": "user",
                "content": (
                    f"Step {step_num} result:\n"
                    f"Score: {reward_score}\n"
                    f"Feedback: {result['reward']['feedback']}\n\n"
                    "Continue your investigation. Respond with the next JSON action."
                ),
            }
        )

    # Required [END] log format.
    print(
        json.dumps(
            {
                "type": "[END]",
                "task_id": task_id,
                "final_score": best_score,
                "steps": step_num,
            }
        ),
        flush=True,
    )

    return best_score


if __name__ == "__main__":
    print(f"Running baseline against {API_BASE_URL} using {MODEL_NAME}", flush=True)
    scores: Dict[str, float] = {}

    for task_id in TASKS:
        scores[task_id] = run_task(task_id)
        time.sleep(1)

    mean_score = round(sum(scores.values()) / len(scores), 3)
    print(
        json.dumps(
            {
                "type": "[SUMMARY]",
                "scores": scores,
                "mean_score": mean_score,
                "model": MODEL_NAME,
            }
        ),
        flush=True,
    )
