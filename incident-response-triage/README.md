---
title: Incident Response Triage
emoji: "🚨"
colorFrom: red
colorTo: orange
sdk: docker
pinned: false
tags:
  - openenv
  - rl-environment
  - incident-response
  - sre
  - agent-benchmark
---

# Incident Response Triage - OpenEnv Environment

An RL environment where an LLM agent diagnoses and remediates production
incidents by reasoning through logs, alerts, and metrics.

## Environment Description

Simulates real SRE workflows. The agent receives a snapshot of a broken
system (alerts, log lines, service metrics, dependency graph) and must
identify the root cause and apply the correct remediation.

## Action Space

| action_type     | Description                              | Key field        |
|-----------------|------------------------------------------|------------------|
| `query_logs`    | Inspect a specific service's logs        | `target_service` |
| `query_metrics` | Inspect a specific service's metrics     | `target_service` |
| `diagnose`      | State the root cause                     | `diagnosis`      |
| `remediate`     | State the fix                            | `remediation`    |
| `escalate`      | Give up and escalate                     | -                |

## Observation Space

```json
{
  "incident_id": "uuid",
  "task_id": "oom_crash",
  "alerts": [{"service": "...", "severity": "critical", "message": "..."}],
  "logs": [{"service": "...", "level": "ERROR", "message": "..."}],
  "metrics": {
    "service-name": {
      "cpu": 0.0,
      "memory": 0.0,
      "error_rate": 0.0,
      "latency_ms": 0.0
    }
  },
  "service_map": {"service": ["dependency1", "dependency2"]},
  "step": 1,
  "max_steps": 10,
  "done": false
}
```

## Tasks

| ID | Name | Difficulty | Description |
|----|------|------------|-------------|
| `oom_crash` | OOM Crash | Easy | Heap exhaustion, single service, no red herrings |
| `db_pool_exhaustion` | DB Pool Exhaustion | Medium | CPU is a red herring; connection pool is the real issue |
| `cascading_failure` | Cascading Failure | Hard | Redis disk full cascades through 3 services to the gateway |

## Reward Function

- Correct diagnosis: +0.5
- Correct remediation: +0.4
- Efficiency bonus (fewer steps): up to +0.1
- Red herring penalty: -0.05 to -0.15
- Repeat action penalty: -0.05

## Setup

```bash
pip install -r requirements.txt
uvicorn server:app --host 0.0.0.0 --port 7860
```

## Run Baseline

```bash
# Linux/macOS
export API_BASE_URL=http://localhost:7860
export MODEL_NAME=gpt-4o-mini
export OPENAI_API_KEY=sk-...
python inference.py
```

```powershell
# Windows PowerShell
$env:API_BASE_URL="http://localhost:7860"
$env:MODEL_NAME="gpt-4o-mini"
$env:OPENAI_API_KEY="sk-..."
python inference.py
```

## Baseline Scores (gpt-4o-mini)

| Task | Score |
|------|-------|
| oom_crash | 0.90 |
| db_pool_exhaustion | 0.72 |
| cascading_failure | 0.55 |
| **Mean** | **0.72** |

## Docker

```bash
docker build -t incident-response-triage .
docker run -p 7860:7860 incident-response-triage
```

## Validation Tip

The most dangerous submission mistake is malformed structured logs. Validate
that every log line is valid JSON and includes exact field names for
`[START]`, `[STEP]`, and `[END]`.
