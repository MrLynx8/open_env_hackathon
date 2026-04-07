from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class Alert(BaseModel):
    service: str
    severity: Literal["critical", "warning", "info"]
    message: str
    timestamp: str


class LogEntry(BaseModel):
    service: str
    level: Literal["ERROR", "WARN", "INFO", "DEBUG"]
    message: str
    timestamp: str


class Observation(BaseModel):
    incident_id: str
    task_id: str
    alerts: List[Alert]
    logs: List[LogEntry]
    # {service_name: {cpu, memory, error_rate, latency_ms}}
    metrics: Dict[str, Dict[str, float]]
    # {service: [list of upstream dependencies]}
    service_map: Dict[str, List[str]]
    step: int
    max_steps: int
    done: bool
    hint: Optional[str] = None


class Action(BaseModel):
    action_type: Literal[
        "diagnose",
        "remediate",
        "query_logs",
        "query_metrics",
        "escalate",
    ]
    diagnosis: Optional[str] = None
    remediation: Optional[str] = None
    target_service: Optional[str] = None
    reasoning: Optional[str] = None


class Reward(BaseModel):
    score: float = Field(ge=0.0, le=1.0)
    breakdown: Dict[str, float]
    feedback: str
    correct_diagnosis: Optional[str] = None
    correct_remediation: Optional[str] = None
