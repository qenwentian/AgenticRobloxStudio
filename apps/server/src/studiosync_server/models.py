"""Relational coordination metadata; source contents are never stored here."""

import time
import uuid

from sqlalchemy import JSON, BigInteger, Float, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))


class Credential(Base):
    __tablename__ = "credentials"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[float] = mapped_column(Float)


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(30), default="GIT_ROJO")
    generation: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Membership(Base):
    __tablename__ = "project_memberships"
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(20))


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    device: Mapped[str] = mapped_column(String(100))
    root_label: Mapped[str] = mapped_column(String(300))
    branch: Mapped[str] = mapped_column(String(200), default="")
    mode: Mapped[str] = mapped_column(String(30))


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("project_id", "user_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(100))
    capabilities: Mapped[list] = mapped_column(JSON, default=list)


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    status: Mapped[str] = mapped_column(String(20), default="ONLINE")
    current_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    last_heartbeat: Mapped[float] = mapped_column(Float, default=time.time)
    expires_at: Mapped[float] = mapped_column(Float)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(10000), default="")
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    assigned_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(20), default="READY")
    blocker_reason: Mapped[str] = mapped_column(String(10000), default="", server_default="")
    resources: Mapped[list] = mapped_column(JSON, default=list)
    acceptance_criteria: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)
    completed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    submission_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class Dependency(Base):
    __tablename__ = "task_dependencies"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    depends_on: Mapped[str] = mapped_column(ForeignKey("tasks.id"), primary_key=True)


class Resource(Base):
    __tablename__ = "resources"
    __table_args__ = (UniqueConstraint("project_id", "key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(1000))
    revision: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Lease(Base):
    __tablename__ = "resource_leases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    mode: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64))
    generation: Mapped[int] = mapped_column(BigInteger)
    expires_at: Mapped[float] = mapped_column(Float, index=True)
    released_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class WritePermit(Base):
    __tablename__ = "write_permits"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    resource_id: Mapped[str] = mapped_column(ForeignKey("resources.id"), index=True)
    lease_id: Mapped[str] = mapped_column(ForeignKey("resource_leases.id"))
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"))
    expected_revision: Mapped[str] = mapped_column(String(64))
    new_revision: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="PENDING")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    sender_session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"))
    recipient_agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    reply_to: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    message_type: Mapped[str] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(String(10000))
    related_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    related_resource: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Receipt(Base):
    __tablename__ = "message_receipts"
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"), primary_key=True)
    acknowledged_at: Mapped[float] = mapped_column(Float, default=time.time)


class Contract(Base):
    __tablename__ = "interface_contracts"
    __table_args__ = (UniqueConstraint("project_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    owner_agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"))
    name: Mapped[str] = mapped_column(String(200))


class ContractVersion(Base):
    __tablename__ = "interface_versions"
    contract_id: Mapped[str] = mapped_column(ForeignKey("interface_contracts.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    description: Mapped[str] = mapped_column(String(10000))
    signatures: Mapped[dict] = mapped_column(JSON)
    errors: Mapped[str] = mapped_column(String(10000), default="")
    compatibility_notes: Mapped[str] = mapped_column(String(10000), default="")
    approval_status: Mapped[str] = mapped_column(String(20), default="PROPOSED")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Consumer(Base):
    __tablename__ = "interface_consumers"
    contract_id: Mapped[str] = mapped_column(ForeignKey("interface_contracts.id"), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), primary_key=True)
    approved_version: Mapped[int] = mapped_column(Integer, default=0)


class Submission(Base):
    __tablename__ = "work_submissions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"))
    summary: Mapped[str] = mapped_column(String(10000))
    work_ref: Mapped[str] = mapped_column(String(500))
    test_results: Mapped[dict] = mapped_column(JSON)
    resources: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Conflict(Base):
    __tablename__ = "conflicts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    category: Mapped[str] = mapped_column(String(30))
    severity: Mapped[str] = mapped_column(String(20), default="WARNING")
    resources: Mapped[list] = mapped_column(JSON)
    agents: Mapped[list] = mapped_column(JSON)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    description: Mapped[str] = mapped_column(String(10000))
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    history: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Event(Base):
    __tablename__ = "events"
    __table_args__ = {"sqlite_autoincrement": True}
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    event_type: Mapped[str] = mapped_column(String(50))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[float] = mapped_column(Float, default=time.time, index=True)
