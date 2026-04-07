import uuid
from typing import List

from models import Action, Alert, LogEntry, Reward

CORRECT_DIAGNOSIS = {
    "db_connection_pool_exhausted",
    "connection_pool_exhaustion",
    "jdbc_pool_exhausted",
    "database_connection_pool_full",
    "hikari_pool_exhausted",
    "connection_pool_full",
}
CORRECT_REMEDIATION = {
    "increase_connection_pool",
    "increase_pool_size",
    "restart_db_proxy",
    "scale_connection_pool",
    "tune_connection_pool",
    "increase_hikari_pool",
}
RED_HERRINGS = {"cpu", "compute", "processor", "high_cpu", "cpu_spike", "cpu_bottleneck"}


class DBPoolTask:
    task_id = "db_pool_exhaustion"

    def get_initial_state(self):
        self._diagnosis_correct = False
        self._remediation_correct = False
        self._red_herring_hit = False
        return {
            "incident_id": str(uuid.uuid4()),
            "alerts": [
                Alert(
                    service="user-service",
                    severity="critical",
                    message="user-service P99 latency > 5000ms - SLA breached",
                    timestamp="2024-01-15T14:22:00Z",
                ),
                Alert(
                    service="user-service",
                    severity="warning",
                    message="CPU usage elevated at 72%",
                    timestamp="2024-01-15T14:21:30Z",
                ),
            ],
            "logs": [
                LogEntry(
                    service="user-service",
                    level="ERROR",
                    message="Unable to acquire JDBC Connection - pool [100/100 in use]",
                    timestamp="2024-01-15T14:21:55Z",
                ),
                LogEntry(
                    service="user-service",
                    level="ERROR",
                    message="HikariPool-1: Connection not available, timed out after 30000ms",
                    timestamp="2024-01-15T14:21:50Z",
                ),
                LogEntry(
                    service="user-service",
                    level="WARN",
                    message="CPU at 72% - possible compute bottleneck",
                    timestamp="2024-01-15T14:21:00Z",
                ),
                LogEntry(
                    service="postgres-db",
                    level="INFO",
                    message="Active connections: 100, max_connections: 100",
                    timestamp="2024-01-15T14:20:45Z",
                ),
            ],
            "metrics": {
                "user-service": {
                    "cpu": 72.0,
                    "memory": 61.0,
                    "error_rate": 85.0,
                    "latency_ms": 5200.0,
                },
                "postgres-db": {
                    "cpu": 38.0,
                    "memory": 55.0,
                    "error_rate": 0.0,
                    "latency_ms": 12.0,
                },
            },
            "service_map": {
                "user-service": ["postgres-db"],
                "postgres-db": [],
            },
        }

    def grade(self, action: Action, step: int, history: List[Action]) -> Reward:
        breakdown = {}

        if action.action_type == "diagnose" and action.diagnosis:
            diagnosis = action.diagnosis.lower().replace(" ", "_").replace("-", "_")
            if any(candidate in diagnosis for candidate in CORRECT_DIAGNOSIS):
                self._diagnosis_correct = True
            elif any(red_herring in diagnosis for red_herring in RED_HERRINGS):
                self._red_herring_hit = True

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
        if self._red_herring_hit and not self._diagnosis_correct:
            score = max(0.0, score - 0.1)
            breakdown["red_herring_penalty"] = -0.1

        if self._diagnosis_correct and self._remediation_correct:
            efficiency = max(0.0, round(0.1 * (1 - (step - 1) / 6.0), 3))
            score = min(1.0, score + efficiency)
            breakdown["efficiency_bonus"] = efficiency

        score = round(min(1.0, max(0.0, score)), 3)
        feedback = (
            "Correct. DB connection pool saturated - CPU was a red herring."
            if self._diagnosis_correct and self._remediation_correct
            else "Good diagnosis. Now remediate."
            if self._diagnosis_correct
            else "Wrong direction. The CPU spike is a red herring - check the JDBC logs."
            if self._red_herring_hit
            else "Keep investigating. Look at what postgres-db is reporting."
        )
        return Reward(
            score=score,
            breakdown=breakdown,
            feedback=feedback,
            correct_diagnosis="db_connection_pool_exhausted" if score >= 0.8 else None,
            correct_remediation="increase_connection_pool" if score >= 0.8 else None,
        )
