import uuid
from typing import List

from models import Action, Alert, LogEntry, Reward

CORRECT_DIAGNOSIS = {
    "memory_exhaustion",
    "oom",
    "out_of_memory",
    "heap_exhaustion",
    "java_heap_space",
    "jvm_oom",
}
CORRECT_REMEDIATION = {
    "restart_service",
    "restart_payment_service",
    "increase_heap",
    "increase_jvm_heap",
    "increase_memory",
    "scale_memory",
    "add_memory",
}


class OOMCrashTask:
    task_id = "oom_crash"

    def get_initial_state(self):
        self._diagnosis_correct = False
        self._remediation_correct = False
        return {
            "incident_id": str(uuid.uuid4()),
            "alerts": [
                Alert(
                    service="payment-service",
                    severity="critical",
                    message="payment-service is DOWN - health check failing",
                    timestamp="2024-01-15T10:32:00Z",
                ),
                Alert(
                    service="payment-service",
                    severity="critical",
                    message="JVM heap memory at 98% - GC unable to free space",
                    timestamp="2024-01-15T10:31:45Z",
                ),
            ],
            "logs": [
                LogEntry(
                    service="payment-service",
                    level="ERROR",
                    message="java.lang.OutOfMemoryError: Java heap space",
                    timestamp="2024-01-15T10:31:50Z",
                ),
                LogEntry(
                    service="payment-service",
                    level="ERROR",
                    message="GC overhead limit exceeded - heap usage 98%",
                    timestamp="2024-01-15T10:31:48Z",
                ),
                LogEntry(
                    service="payment-service",
                    level="WARN",
                    message="Heap growing fast: 2GB -> 3.8GB over 30 minutes",
                    timestamp="2024-01-15T10:15:00Z",
                ),
                LogEntry(
                    service="payment-service",
                    level="INFO",
                    message="Request volume: 450 req/s (normal: 200 req/s)",
                    timestamp="2024-01-15T10:10:00Z",
                ),
            ],
            "metrics": {
                "payment-service": {
                    "cpu": 45.0,
                    "memory": 98.2,
                    "error_rate": 100.0,
                    "latency_ms": 0.0,
                }
            },
            "service_map": {"payment-service": ["postgres-db", "redis-cache"]},
        }

    def grade(self, action: Action, step: int, history: List[Action]) -> Reward:
        breakdown = {}

        if action.action_type == "diagnose" and action.diagnosis:
            diagnosis = action.diagnosis.lower().replace(" ", "_").replace("-", "_")
            if any(candidate in diagnosis for candidate in CORRECT_DIAGNOSIS):
                self._diagnosis_correct = True

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

        if self._diagnosis_correct and self._remediation_correct:
            efficiency = max(0.0, round(0.1 * (1 - (step - 1) / 5.0), 3))
            score = min(1.0, score + efficiency)
            breakdown["efficiency_bonus"] = efficiency

        if (
            len(history) >= 1
            and history[-1].action_type == action.action_type
            and action.action_type in ("diagnose", "remediate")
        ):
            penalty = 0.05
            score = max(0.0, score - penalty)
            breakdown["repeat_penalty"] = -penalty

        score = round(min(1.0, max(0.0, score)), 3)
        feedback = (
            "Correct. OOM from heap exhaustion - restart or increase heap resolves it."
            if self._diagnosis_correct and self._remediation_correct
            else "Good diagnosis. Now provide the remediation action."
            if self._diagnosis_correct
            else "Incorrect diagnosis. Look at the JVM heap metrics and error logs."
        )
        return Reward(
            score=score,
            breakdown=breakdown,
            feedback=feedback,
            correct_diagnosis="memory_exhaustion" if score >= 0.85 else None,
            correct_remediation="restart_service" if score >= 0.85 else None,
        )
