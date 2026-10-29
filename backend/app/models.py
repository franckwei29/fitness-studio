"""Persistent identity, immutable facts, and workflow records."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


def uid() -> str:
    return str(uuid4())


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class Record:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=now)


class User(Record, Base):
    __tablename__ = "users"
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(20), default="Member")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class LoginSession(Record, Base):
    __tablename__ = "login_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class MemberGroup(Record, Base):
    __tablename__ = "member_groups"
    name: Mapped[str] = mapped_column(String(80), unique=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    configuration: Mapped[dict] = mapped_column(JSON)


class Member(Record, Base):
    __tablename__ = "members"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True)
    group_id: Mapped[str] = mapped_column(ForeignKey("member_groups.id"))
    status: Mapped[str] = mapped_column(String(30), default="Accepted")
    profile_version: Mapped[int] = mapped_column(Integer, default=1)


class ProfileSnapshot(Record, Base):
    __tablename__ = "profile_snapshots"
    __table_args__ = (UniqueConstraint("member_id", "version"),)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"))
    version: Mapped[int] = mapped_column(Integer)
    facts: Mapped[dict] = mapped_column(JSON)
    sources: Mapped[dict] = mapped_column(JSON)
    confirmed_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class Assessment(Record, Base):
    __tablename__ = "intake_assessments"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(30), default="Collecting")
    proposed_facts: Mapped[dict] = mapped_column(JSON, default=dict)
    confirmed_facts: Mapped[dict] = mapped_column(JSON, default=dict)
    decision: Mapped[dict | None] = mapped_column(JSON)


class ChatSession(Record, Base):
    __tablename__ = "chat_sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    active_role: Mapped[str] = mapped_column(String(30), default="Intake")
    title: Mapped[str] = mapped_column(String(160), default="Your fitness journey")


class Message(Record, Base):
    __tablename__ = "messages"
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id"))
    sender: Mapped[str] = mapped_column(String(30))
    content: Mapped[str] = mapped_column(Text)
    cards: Mapped[list] = mapped_column(JSON, default=list)


class Request(Record, Base):
    __tablename__ = "requests"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    session_id: Mapped[str | None] = mapped_column(ForeignKey("chat_sessions.id"))
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="Queued")
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    max_model_calls: Mapped[int] = mapped_column(Integer, default=12)
    revisions: Mapped[int] = mapped_column(Integer, default=0)
    refreshes: Mapped[int] = mapped_column(Integer, default=0)
    deadline: Mapped[datetime] = mapped_column(DateTime)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict | None] = mapped_column(JSON)


class WorkItem(Record, Base):
    __tablename__ = "work_items"
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    role: Mapped[str] = mapped_column(String(30))
    subject_kind: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(30), default="Queued")
    wait_reason: Mapped[str | None] = mapped_column(String(80))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    fence: Mapped[int] = mapped_column(Integer, default=0)
    input_versions: Mapped[dict] = mapped_column(JSON, default=dict)


class AuditEvent(Record, Base):
    __tablename__ = "audit_events"
    actor_id: Mapped[str] = mapped_column(String(36))
    request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    subject_id: Mapped[str | None] = mapped_column(String(36))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class IdempotencyRecord(Record, Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("user_id", "operation", "key"),)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    operation: Mapped[str] = mapped_column(String(100))
    key: Mapped[str] = mapped_column(String(128))
    fingerprint: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)


class PlanVersion(Record, Base):
    __tablename__ = "plan_versions"
    __table_args__ = (UniqueConstraint("member_id", "kind", "version"),)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"))
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"))
    kind: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30), default="Draft")
    profile_version: Mapped[int] = mapped_column(Integer)
    config_version: Mapped[int] = mapped_column(Integer)
    agent_version_id: Mapped[str] = mapped_column(String(36))
    baseline_id: Mapped[str | None] = mapped_column(ForeignKey("plan_versions.id"))
    training_input_id: Mapped[str | None] = mapped_column(ForeignKey("plan_versions.id"))
    content: Mapped[dict] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))


class ValidationResult(Record, Base):
    __tablename__ = "validation_results"
    subject_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    rule_version: Mapped[str] = mapped_column(String(80))
    passed: Mapped[bool] = mapped_column(Boolean)
    violations: Mapped[list] = mapped_column(JSON)
    basis: Mapped[dict] = mapped_column(JSON)


class PlanPair(Record, Base):
    __tablename__ = "plan_pairs"
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"))
    training_id: Mapped[str] = mapped_column(ForeignKey("plan_versions.id"))
    nutrition_id: Mapped[str] = mapped_column(ForeignKey("plan_versions.id"))
    validation_id: Mapped[str] = mapped_column(ForeignKey("validation_results.id"))
    status: Mapped[str] = mapped_column(String(30), default="Candidate")


class CurrentPlans(Base):
    __tablename__ = "current_plans"
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"), primary_key=True)
    training_id: Mapped[str | None] = mapped_column(ForeignKey("plan_versions.id"))
    nutrition_id: Mapped[str | None] = mapped_column(ForeignKey("plan_versions.id"))
    pair_id: Mapped[str | None] = mapped_column(ForeignKey("plan_pairs.id"))
    version: Mapped[int] = mapped_column(Integer, default=0)


class Activation(Record, Base):
    __tablename__ = "activations"
    operation_key: Mapped[str] = mapped_column(String(128), unique=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"))
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"))
    result: Mapped[dict] = mapped_column(JSON)


class Approval(Record, Base):
    __tablename__ = "approvals"
    subject_kind: Mapped[str] = mapped_column(String(40))
    subject_id: Mapped[str] = mapped_column(String(36), index=True)
    subject_version: Mapped[int] = mapped_column(Integer)
    fingerprint: Mapped[str] = mapped_column(String(64))
    scope: Mapped[dict] = mapped_column(JSON)
    context: Mapped[dict] = mapped_column(JSON)
    decision: Mapped[str] = mapped_column(String(30), default="Pending")
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    comment: Mapped[str | None] = mapped_column(Text)


class AgentVersion(Record, Base):
    __tablename__ = "agent_versions"
    role: Mapped[str] = mapped_column(String(30))
    label: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(20), default="gemini", server_default="gemini")
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    reasoning_effort: Mapped[str | None] = mapped_column(String(20))
    prompt: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))


class ChangeRequest(Record, Base):
    __tablename__ = "change_requests"
    kind: Mapped[str] = mapped_column(String(40))
    group_id: Mapped[str] = mapped_column(ForeignKey("member_groups.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    baseline: Mapped[dict] = mapped_column(JSON)
    candidate: Mapped[dict] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="Preparing")
    reason: Mapped[str] = mapped_column(Text)
    evaluation: Mapped[dict | None] = mapped_column(JSON)
    execution: Mapped[dict | None] = mapped_column(JSON)


class Incident(Record, Base):
    __tablename__ = "incidents"
    event_key: Mapped[str] = mapped_column(String(128), unique=True)
    request_id: Mapped[str | None] = mapped_column(ForeignKey("requests.id"))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="Open")
    outcome: Mapped[str] = mapped_column(String(30), default="Unresolved")
    evidence: Mapped[dict] = mapped_column(JSON)
    analysis: Mapped[dict | None] = mapped_column(JSON)
    recovery: Mapped[dict | None] = mapped_column(JSON)


class BudgetAccount(Record, Base):
    __tablename__ = "budget_accounts"
    scope_key: Mapped[str] = mapped_column(String(100), unique=True)
    limit_micros: Mapped[int] = mapped_column(Integer, default=0)
    reserved_micros: Mapped[int] = mapped_column(Integer, default=0)
    spent_micros: Mapped[int] = mapped_column(Integer, default=0)
    currency: Mapped[str] = mapped_column(String(3), default="USD")


class ModelCall(Record, Base):
    __tablename__ = "model_calls"
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(20), default="gemini", server_default="gemini")
    status: Mapped[str] = mapped_column(String(30), default="Reserved")
    reserved_micros: Mapped[int] = mapped_column(Integer)
    actual_micros: Mapped[int | None] = mapped_column(Integer)
    price_version: Mapped[str] = mapped_column(String(80))
    output: Mapped[dict | None] = mapped_column(JSON)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    usage: Mapped[dict | None] = mapped_column(JSON)
    provider_id: Mapped[str | None] = mapped_column(String(200))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class BudgetReservation(Record, Base):
    __tablename__ = "budget_reservations"
    __table_args__ = (UniqueConstraint("account_id", "call_id"),)
    account_id: Mapped[str] = mapped_column(ForeignKey("budget_accounts.id"))
    call_id: Mapped[str] = mapped_column(ForeignKey("model_calls.id"))
    amount_micros: Mapped[int] = mapped_column(Integer)
    settled: Mapped[bool] = mapped_column(Boolean, default=False)


class RequestEvent(Base):
    __tablename__ = "request_events"
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    request_id: Mapped[str] = mapped_column(ForeignKey("requests.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), default=now)


class Notification(Record, Base):
    __tablename__ = "notifications"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    business_key: Mapped[str] = mapped_column(String(160), unique=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(40))
    read_at: Mapped[datetime | None] = mapped_column(DateTime)


class Observation(Record, Base):
    __tablename__ = "observations"
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"))
    request_id: Mapped[str | None] = mapped_column(ForeignKey("requests.id"))
    kind: Mapped[str] = mapped_column(String(40))
    content: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="Confirmed")


class EvaluationRun(Record, Base):
    __tablename__ = "evaluation_runs"
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(30))
    baseline_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"))
    candidate_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"))
    baseline_fingerprint: Mapped[str] = mapped_column(String(64))
    candidate_fingerprint: Mapped[str] = mapped_column(String(64))
    suite_version: Mapped[str] = mapped_column(String(80))
    suite_fingerprint: Mapped[str] = mapped_column(String(64))
    cases: Mapped[list] = mapped_column(JSON)
    evidence_mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="Prepared")
    summary: Mapped[dict | None] = mapped_column(JSON)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class EvaluationResult(Record, Base):
    __tablename__ = "evaluation_results"
    __table_args__ = (UniqueConstraint("run_id", "agent_version_id", "case_key"),)
    run_id: Mapped[str] = mapped_column(ForeignKey("evaluation_runs.id"))
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"))
    case_key: Mapped[str] = mapped_column(String(80))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    call_id: Mapped[str | None] = mapped_column(ForeignKey("model_calls.id"))
    output: Mapped[dict | None] = mapped_column(JSON)
    checks: Mapped[dict] = mapped_column(JSON)
    passed: Mapped[bool] = mapped_column(Boolean)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(80))
