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

SYSTEM_PROMPT = """You are an expert SRE (Site Reliability Engineer).
You receive a production incident with logs, metrics, alerts, and a service dependency map.

Your goal: identify the ROOT CAUSE and apply the correct FIX.

Available action_types:
- \"query_logs\"    -> inspect a specific service's logs (set target_service)
- \"query_metrics\" -> inspect a specific service's metrics (set target_service)
- \"diagnose\"      -> state your root cause (set diagnosis as snake_case string)
- \"remediate\"     -> state your fix (set remediation as snake_case string)
- \"escalate\"      -> if you truly cannot determine the cause

Strategy:
1. First scan the service_map to understand dependencies
2. Trace errors UPSTREAM from the most visible symptom
3. Diagnose once you're confident, then remediate

Always respond with ONLY a JSON object - no markdown, no explanation:
{
  \"action_type\": \"...\",
  \"diagnosis\": \"snake_case_root_cause_or_null\",
  \"remediation\": \"snake_case_fix_or_null\",
  \"target_service\": \"service-name-or-null\",
  \"reasoning\": \"one sentence explaining your choice\"
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

        step_resp = requests.post(f"{API_BASE_URL}/step", json=action_payload, timeout=30)
        step_resp.raise_for_status()
        result: Dict[str, Any] = step_resp.json()

        reward_score = float(result["reward"]["score"])
        done = bool(result["done"])
        best_score = max(best_score, reward_score)

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
