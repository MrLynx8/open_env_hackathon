import uuid
from typing import List

from models import Action, Alert, LogEntry, Reward

# Root cause: Redis ran out of disk space, halting writes.
# This cascaded: inventory -> order -> api-gateway.
CORRECT_DIAGNOSIS = {
    "redis_failure",
    "redis_down",
    "cache_failure",
    "redis_disk_full",
    "redis_disk_exhaustion",
    "redis_oom_disk",
    "redis_out_of_disk",
    "redis_no_space",
}
CORRECT_REMEDIATION = {
    "restart_redis",
    "failover_redis",
    "clear_redis_disk",
    "free_redis_disk",
    "restore_redis",
    "fix_redis_disk",
    "scale_redis_storage",
    "increase_redis_disk",
}


class CascadingFailureTask:
    task_id = "cascading_failure"

    def get_initial_state(self):
        self._diagnosis_correct = False
        self._remediation_correct = False
        self._investigated_redis = False
        return {
            "incident_id": str(uuid.uuid4()),
            "alerts": [
                Alert(
                    service="api-gateway",
                    severity="critical",
                    message="api-gateway error rate 94% - SLA breached",
                    timestamp="2024-01-15T18:45:00Z",
                ),
                Alert(
                    service="order-service",
                    severity="critical",
                    message="order-service queue depth 50,000 - processing halted",
                    timestamp="2024-01-15T18:44:30Z",
                ),
                Alert(
                    service="inventory-service",
                    severity="warning",
                    message="inventory-service timeouts increasing",
                    timestamp="2024-01-15T18:43:00Z",
                ),
            ],
            "logs": [
                LogEntry(
                    service="api-gateway",
                    level="ERROR",
                    message="Upstream order-service timeout after 30s",
                    timestamp="2024-01-15T18:44:55Z",
                ),
                LogEntry(
                    service="order-service",
                    level="ERROR",
                    message="inventory-service call failed: connection refused",
                    timestamp="2024-01-15T18:44:20Z",
                ),
                LogEntry(
                    service="order-service",
                    level="WARN",
                    message="Retry queue backing up: 50,241 pending jobs",
                    timestamp="2024-01-15T18:44:10Z",
                ),
                LogEntry(
                    service="inventory-service",
                    level="ERROR",
                    message="Redis connection refused: redis:6379 - cannot read stock levels",
                    timestamp="2024-01-15T18:43:05Z",
                ),
                LogEntry(
                    service="inventory-service",
                    level="ERROR",
                    message="DB fallback also failing - all stock reads returning 503",
                    timestamp="2024-01-15T18:43:08Z",
                ),
                LogEntry(
                    service="redis-cache",
                    level="ERROR",
                    message="MISCONF: Redis can't save RDB snapshot. Stopping all writes.",
                    timestamp="2024-01-15T18:42:50Z",
                ),
                LogEntry(
                    service="redis-cache",
                    level="ERROR",
                    message="AOF rewrite failed: No space left on device. Entering read-only mode.",
                    timestamp="2024-01-15T18:42:48Z",
                ),
            ],
            "metrics": {
                "api-gateway": {
                    "cpu": 30.0,
                    "memory": 45.0,
                    "error_rate": 94.0,
                    "latency_ms": 31000.0,
                },
                "order-service": {
                    "cpu": 85.0,
                    "memory": 78.0,
                    "error_rate": 88.0,
                    "latency_ms": 28000.0,
                },
                "inventory-service": {
                    "cpu": 20.0,
                    "memory": 40.0,
                    "error_rate": 100.0,
                    "latency_ms": 0.0,
                },
                "redis-cache": {
                    "cpu": 2.0,
                    "memory": 12.0,
                    "error_rate": 100.0,
                    "latency_ms": 0.0,
                },
            },
            "service_map": {
                "api-gateway": ["order-service"],
                "order-service": ["inventory-service"],
                "inventory-service": ["redis-cache", "postgres-db"],
                "redis-cache": [],
                "postgres-db": [],
            },
        }

    def grade(self, action: Action, step: int, history: List[Action]) -> Reward:
        breakdown = {}
        blame_score_adj = 0.0

        if action.action_type in ("query_logs", "query_metrics") and action.target_service:
            target_service = action.target_service.lower().replace(" ", "-")
            if "redis" in target_service or "cache" in target_service:
                self._investigated_redis = True

        if action.action_type == "diagnose" and action.diagnosis:
            diagnosis = action.diagnosis.lower().replace(" ", "_").replace("-", "_")
            if any(candidate in diagnosis for candidate in CORRECT_DIAGNOSIS):
                self._diagnosis_correct = True
            elif "api" in diagnosis or "gateway" in diagnosis:
                blame_score_adj = -0.15
            elif "order" in diagnosis:
                blame_score_adj = -0.05
            elif "inventory" in diagnosis:
                blame_score_adj = 0.05

        if action.action_type == "remediate" and action.remediation:
            remediation = action.remediation.lower().replace(" ", "_").replace("-", "_")
            if any(candidate in remediation for candidate in CORRECT_REMEDIATION):
                self._remediation_correct = True

        score = 0.0
        if self._diagnosis_correct:
            score += 0.5
            breakdown["diagnosis"] = 0.5
        if self._remediation_correct:
            score += 0.4
            breakdown["remediation"] = 0.4
        if self._investigated_redis and not self._diagnosis_correct:
            score = max(score, 0.15)
            breakdown["upstream_investigation"] = 0.15
        if blame_score_adj != 0 and not self._diagnosis_correct:
            score = max(0.0, score + blame_score_adj)
            breakdown["layer_penalty"] = blame_score_adj

        if self._diagnosis_correct and self._remediation_correct:
            efficiency = max(0.0, round(0.1 * (1 - (step - 1) / 8.0), 3))
            score = min(1.0, score + efficiency)
            breakdown["efficiency_bonus"] = efficiency

        score = round(min(1.0, max(0.0, score)), 3)
        feedback = (
            "Correct. Redis disk exhaustion caused the cascade."
            if self._diagnosis_correct and self._remediation_correct
            else "Great diagnosis. Now fix Redis."
            if self._diagnosis_correct
            else "You are looking in the right area - what exactly is Redis reporting?"
            if self._investigated_redis
            else "Trace upstream: api-gateway -> order -> inventory -> redis. Start from the deepest dependency."
        )
        return Reward(
            score=score,
            breakdown=breakdown,
            feedback=feedback,
            correct_diagnosis="redis_disk_exhaustion" if score >= 0.85 else None,
            correct_remediation="clear_redis_disk" if score >= 0.85 else None,
        )
