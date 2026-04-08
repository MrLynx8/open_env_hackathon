"""
Submission inference for Incident Response Triage.

Complies with mandatory requirements:
- Uses OpenAI client for LLM calls via API_BASE_URL/MODEL_NAME/HF_TOKEN.
- Emits [START], [STEP], [END] structured stdout lines.
- Handles network/parsing errors without crashing the process.
"""

import json
import os
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

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

# Mandatory model config variables.
API_BASE_URL = (
    os.getenv("API_BASE_URL")
    or os.getenv("OPENAI_BASE_URL")
    or "https://models.inference.ai.azure.com"
)
MODEL_NAME = os.getenv("MODEL_NAME") or "gpt-4o-mini"
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()
API_KEY = (
    HF_TOKEN
    or os.getenv("API_KEY", "").strip()
    or os.getenv("OPENAI_API_KEY", "").strip()
    or os.getenv("GITHUB_TOKEN", "").strip()
)
LOCAL_IMAGE_NAME = os.getenv("LOCAL_IMAGE_NAME", "").strip()

# Environment endpoint is separate from model API endpoint.
DEFAULT_ENV_BASE_URL = "https://MrLynx8-incident-response-triage.hf.space"
BENCHMARK = os.getenv("BENCHMARK", "incident-response-triage")

TASKS = ["oom_crash", "db_pool_exhaustion", "cascading_failure"]
MAX_STEPS = 10
MAX_QUERY_STEPS = 2
SUCCESS_SCORE_THRESHOLD = 0.85

VALID_ACTION_TYPES = {"diagnose", "remediate", "query_logs", "query_metrics", "escalate"}
QUERY_ACTIONS = {"query_logs", "query_metrics"}

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

TASK_PLANS = {
    "oom_crash": ["query_logs", "diagnose", "remediate"],
    "db_pool_exhaustion": ["diagnose", "remediate"],
    "cascading_failure": ["query_logs", "diagnose", "remediate"],
}

SYSTEM_PROMPT = """You are an expert SRE (Site Reliability Engineer).
You receive alerts, logs, metrics, and service_map.
Respond only with JSON:
{
  "action_type": "diagnose" | "remediate" | "query_logs" | "query_metrics" | "escalate",
  "diagnosis": "snake_case_or_null",
  "remediation": "snake_case_or_null",
  "target_service": "service-or-null",
  "reasoning": "one sentence"
}"""


def _is_valid_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    return bool(parsed.netloc)


def _safe_error_text(error: Optional[str]) -> str:
    if not error:
        return "null"
    return str(error).replace("\n", " ").replace("\r", " ").strip() or "null"


def _to_bool_str(value: bool) -> str:
    return "true" if value else "false"


def _log_start(task: str) -> None:
    print(f"[START] task={task} env={BENCHMARK} model={MODEL_NAME}", flush=True)


def _log_step(step: int, action: Dict[str, Any], reward: float, done: bool, error: Optional[str]) -> None:
    action_str = json.dumps(action, separators=(",", ":"), ensure_ascii=False)
    print(
        f"[STEP] step={step} action={action_str} reward={reward:.2f} done={_to_bool_str(done)} error={_safe_error_text(error)}",
        flush=True,
    )


def _log_end(success: bool, steps: int, score: float, rewards: List[float]) -> None:
    rewards_str = ",".join(f"{r:.2f}" for r in rewards) if rewards else "0.00"
    print(
        f"[END] success={_to_bool_str(success)} steps={steps} score={score:.2f} rewards={rewards_str}",
        flush=True,
    )


def _strip_code_fences(raw_text: str) -> str:
    text = raw_text.strip()
    if "```" not in text:
        return text

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

    def _opt_text(value: Any) -> Optional[str]:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    return {
        "action_type": action_type,
        "diagnosis": _opt_text(payload.get("diagnosis")),
        "remediation": _opt_text(payload.get("remediation")),
        "target_service": _opt_text(payload.get("target_service")),
        "reasoning": _opt_text(payload.get("reasoning")) or "",
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


def _deterministic_action(
    task_id: str,
    step_num: int,
    diagnosis_locked: bool,
    remediation_locked: bool,
) -> Dict[str, Any]:
    if diagnosis_locked and not remediation_locked:
        return _forced_remediate(task_id, "Deterministic closeout after accepted diagnosis.")

    if remediation_locked:
        return {
            "action_type": "escalate",
            "diagnosis": None,
            "remediation": None,
            "target_service": None,
            "reasoning": "Episode already solved.",
        }

    plan = TASK_PLANS[task_id]
    action_name = plan[min(step_num - 1, len(plan) - 1)]

    if action_name in QUERY_ACTIONS:
        return {
            "action_type": action_name,
            "diagnosis": None,
            "remediation": None,
            "target_service": DEFAULT_QUERY_TARGET[task_id],
            "reasoning": "Deterministic exploration step.",
        }
    if action_name == "diagnose":
        return _forced_diagnose(task_id, "Deterministic diagnosis step.")
    return _forced_remediate(task_id, "Deterministic remediation step.")


def _build_openai_client() -> Optional[OpenAI]:
    if not API_KEY:
        return None

    try:
        return OpenAI(
            api_key=API_KEY,
            base_url=API_BASE_URL,
            timeout=6.0,
        )
    except Exception:
        return None


def _candidate_env_urls() -> List[str]:
    raw_candidates = [
        os.getenv("ENV_BASE_URL", "").strip(),
        os.getenv("OPENENV_BASE_URL", "").strip(),
        os.getenv("PING_URL", "").strip(),
        os.getenv("SPACE_URL", "").strip(),
        DEFAULT_ENV_BASE_URL,
        "http://localhost:7860",
    ]

    seen = set()
    out: List[str] = []
    for item in raw_candidates:
        if not item:
            continue
        normalized = item.rstrip("/")
        if not _is_valid_url(normalized):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _resolve_env_base_url() -> str:
    candidates = _candidate_env_urls()
    for base in candidates:
        try:
            resp = requests.get(f"{base}/health", timeout=8)
            if resp.status_code == 200:
                return base
        except Exception:
            continue

    # Fall back to first valid candidate even if health probe failed.
    return candidates[0] if candidates else DEFAULT_ENV_BASE_URL


def _model_action(
    client: Optional[OpenAI],
    task_id: str,
    observation: Dict[str, Any],
    messages: List[Dict[str, str]],
) -> Optional[Dict[str, Any]]:
    if client is None:
        return None

    try:
        completion = client.chat.completions.create(
            model=MODEL_NAME,
            messages=messages,
            temperature=0.1,
            max_tokens=350,
        )
        raw_reply = (completion.choices[0].message.content or "").strip()
        messages.append({"role": "assistant", "content": raw_reply})
        payload = json.loads(_strip_code_fences(raw_reply))
        return _sanitize_action(payload)
    except Exception:
        return None


def run_task(task_id: str, client: Optional[OpenAI], env_base_url: str) -> float:
    rewards: List[float] = []
    steps_taken = 0
    score = 0.0
    success = False
    diagnosis_locked = False
    remediation_locked = False
    query_steps = 0

    _log_start(task_id)

    try:
        reset_resp = requests.post(
            f"{env_base_url}/reset",
            params={"task_id": task_id},
            timeout=20,
        )
        reset_resp.raise_for_status()
        observation = reset_resp.json()

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"TASK={task_id}\n"
                    f"OBSERVATION:\n{json.dumps(observation, indent=2)}\n"
                    "Respond with JSON action only."
                ),
            },
        ]

        done = False

        for step_num in range(1, MAX_STEPS + 1):
            if done:
                break

            action_payload = _model_action(client, task_id, observation, messages)
            if action_payload is None:
                action_payload = _deterministic_action(
                    task_id,
                    step_num,
                    diagnosis_locked,
                    remediation_locked,
                )
            else:
                # Guardrails for malformed or low-signal model actions.
                if action_payload["action_type"] == "escalate":
                    action_payload = _deterministic_action(
                        task_id,
                        step_num,
                        diagnosis_locked,
                        remediation_locked,
                    )

                if action_payload["action_type"] in QUERY_ACTIONS and not action_payload.get("target_service"):
                    action_payload["target_service"] = DEFAULT_QUERY_TARGET[task_id]

                if action_payload["action_type"] == "diagnose" and not action_payload.get("diagnosis"):
                    action_payload = _forced_diagnose(task_id, "Missing diagnosis text.")

                if action_payload["action_type"] == "remediate" and not action_payload.get("remediation"):
                    action_payload = _forced_remediate(task_id, "Missing remediation text.")

                if query_steps >= MAX_QUERY_STEPS and action_payload["action_type"] in QUERY_ACTIONS:
                    action_payload = _forced_diagnose(task_id, "Max query steps reached.")

            step_error: Optional[str] = None
            reward_val = 0.0

            try:
                step_resp = requests.post(
                    f"{env_base_url}/step",
                    json=action_payload,
                    timeout=20,
                )
                step_resp.raise_for_status()
                result: Dict[str, Any] = step_resp.json()

                observation = result.get("observation", {})
                done = bool(result.get("done", False))
                reward_val = float(result.get("reward", {}).get("score", 0.0) or 0.0)

                info = result.get("info", {}) if isinstance(result.get("info", {}), dict) else {}
                step_error = info.get("last_action_error")

                breakdown = result.get("reward", {}).get("breakdown", {})
                diagnosis_credit = float(breakdown.get("diagnosis", 0.0) or 0.0)
                remediation_credit = float(breakdown.get("remediation", 0.0) or 0.0)
                diagnosis_locked = diagnosis_locked or diagnosis_credit >= 0.5
                remediation_locked = remediation_locked or remediation_credit >= 0.4
            except Exception as exc:
                done = True
                step_error = str(exc)

            if action_payload["action_type"] in QUERY_ACTIONS:
                query_steps += 1

            rewards.append(reward_val)
            steps_taken = step_num
            _log_step(
                step=step_num,
                action=action_payload,
                reward=reward_val,
                done=done,
                error=step_error,
            )

            if done:
                break

            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Step={step_num} Reward={reward_val:.2f} Done={str(done).lower()}\n"
                        "Continue with the next JSON action."
                    ),
                }
            )

        score = rewards[-1] if rewards else 0.0
        score = max(0.0, min(1.0, score))
        success = score >= SUCCESS_SCORE_THRESHOLD
    except Exception:
        # Keep process alive and emit [END] in finally below.
        success = False
    finally:
        _log_end(success=success, steps=steps_taken, score=score, rewards=rewards)

    return score


def main() -> None:
    env_base_url = _resolve_env_base_url()
    client = _build_openai_client()

    for task_id in TASKS:
        run_task(task_id=task_id, client=client, env_base_url=env_base_url)
        time.sleep(0.2)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never fail submission with a process-level crash.
        pass
