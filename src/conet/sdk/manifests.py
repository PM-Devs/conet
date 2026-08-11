import uuid
from datetime import datetime, timezone
from typing import Any, Literal

import jsonschema
from pydantic import BaseModel, Field, model_validator

TaskState = Literal[
    'CREATED', 'AUTHORIZING', 'WAITING_APPROVAL', 'ROUTING', 'RUNNING',
    'COMPLETED', 'REJECTED', 'FAILED', 'CANCELLING', 'CANCELLED', 'TIMED_OUT',
]

AuditOutcome = Literal['OK', 'DENIED', 'FAILED', 'CANCELLED']


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SkillDef(BaseModel):
    skill_id: str
    version: str
    summary: str = ''
    execution_modes: list[str] = ['unary']
    side_effects: Literal['read_only', 'idempotent_write', 'unsafe_write']
    idempotency: Literal['not_required', 'key_required'] = 'not_required'
    input_schema: dict
    output_schema: dict
    tags: list[str] = []

    def to_json_schema(self) -> dict:
        return {'input_schema': self.input_schema, 'output_schema': self.output_schema}

    def validate_input(self, payload: dict) -> None:
        jsonschema.validate(instance=payload, schema=self.input_schema)

    def validate_output(self, payload: dict) -> None:
        jsonschema.validate(instance=payload, schema=self.output_schema)


class AgentManifest(BaseModel):
    manifest_version: str = '0.1'
    name: str
    framework: str
    department: str
    role: str = 'worker'
    version: str
    endpoint: str
    identity_ref: str
    lease_ttl_seconds: int = 30
    skills: list[SkillDef] = []

    @model_validator(mode='after')
    def worker_requires_skills(self):
        if self.role == 'worker' and not self.skills:
            raise ValueError("role='worker' requires at least one skill")
        return self


class Task(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    requester: str
    provider: str | None = None
    skill_id: str
    state: TaskState = 'CREATED'
    deadline: datetime | None = None
    trace_id: str | None = None
    idempotency_key: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class AuditEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    actor: str
    action: str
    resource: str
    outcome: AuditOutcome
    timestamp: datetime = Field(default_factory=_utcnow)
    trace_id: str | None = None
    metadata: dict[str, Any] = {}


ApprovalState = Literal['PENDING', 'APPROVED', 'REJECTED', 'EXPIRED']


class Approval(BaseModel):
    approval_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    policy_id: str | None = None
    approvers: list[str] = []
    state: ApprovalState = 'PENDING'
    expires_at: datetime
    decision_metadata: dict[str, Any] = {}
    created_at: datetime = Field(default_factory=_utcnow)
