from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Error(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Result(BaseModel):
    ok: bool = True
    data: dict[str, Any] = Field(default_factory=dict)
    error: Error | None = None


class Account(Input):
    name: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=12, max_length=200)


class ProjectInput(Input):
    name: str = Field(min_length=1, max_length=100)
    mode: Literal["GIT_ROJO", "TEAM_CREATE"] = "GIT_ROJO"


class MemberInput(Input):
    user_id: str
    role: Literal["OWNER", "DEVELOPER", "VIEWER"] = "DEVELOPER"


class Page(Input):
    cursor: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=200)


class WorkspaceInput(Input):
    device: str = Field(min_length=1, max_length=100)
    root_label: str = Field(min_length=1, max_length=300)
    branch: str = Field(default="", max_length=200)
    mode: Literal["GIT_ROJO", "TEAM_CREATE"] = "GIT_ROJO"


class RegisterAgent(Input):
    display_name: str = Field(min_length=1, max_length=100)
    workspace_id: str
    capabilities: list[str] = Field(default_factory=list, max_length=30)


class SessionInput(Input):
    session_id: str


class Heartbeat(SessionInput):
    status: Literal["ONLINE", "WORKING", "WAITING", "BLOCKED", "REVIEWING"] = "ONLINE"


class TaskInput(SessionInput):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    priority: int = Field(default=2, ge=0, le=4)
    resources: list[str] = Field(default_factory=list, max_length=100)
    acceptance_criteria: list[str] = Field(default_factory=list, max_length=100)
    dependencies: list[str] = Field(default_factory=list, max_length=100)
    assigned_agent_id: str | None = None


class TaskRef(SessionInput):
    task_id: str


class TaskUpdate(TaskRef):
    status: Literal[
        "BACKLOG",
        "READY",
        "CLAIMED",
        "IN_PROGRESS",
        "BLOCKED",
        "IN_REVIEW",
        "COMPLETED",
        "CANCELLED",
    ]
    assigned_agent_id: str | None = None
    reason: str = Field(default="", max_length=1000)


class AddDependency(TaskRef):
    depends_on: str


class Acquire(SessionInput):
    resource: str = Field(min_length=3, max_length=1000)
    mode: Literal["READ", "WRITE", "INTENT"] = "WRITE"
    task_id: str | None = None
    ttl: int = Field(default=60, ge=2, le=300)


class ResourceInput(Input):
    resource: str = Field(min_length=3, max_length=1000)


class LeaseInput(SessionInput):
    lease_id: str
    lease_token: str
    generation: int = Field(ge=1)


class Renew(LeaseInput):
    ttl: int = Field(default=60, ge=2, le=300)


class Override(Input):
    lease_id: str
    reason: str = Field(min_length=1, max_length=1000)


class SendMessage(SessionInput):
    recipient_agent_id: str | None = None
    message_type: Literal[
        "DIRECT",
        "ANNOUNCEMENT",
        "TRANSFER_REQUEST",
        "INTERFACE_REQUEST",
        "BLOCKER",
        "COMPLETION",
        "DECISION",
        "REVIEW_REQUEST",
    ] = "DIRECT"
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=10000)
    reply_to: str | None = None
    related_task_id: str | None = None
    related_resource: str | None = None


class Inbox(SessionInput, Page):
    pass


class MessageRef(SessionInput):
    message_id: str


class InterfaceInput(SessionInput):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    signatures: dict[str, Any]
    errors: str = Field(default="", max_length=10000)
    compatibility_notes: str = Field(default="", max_length=10000)
    consumers: list[str] = Field(default_factory=list, max_length=100)


class InterfaceRef(Input):
    name: str = Field(min_length=1, max_length=200)


class InterfaceApproval(SessionInput, InterfaceRef):
    version: int = Field(ge=1)


class Blocker(TaskRef):
    reason: str = Field(min_length=1, max_length=10000)


class Submit(TaskRef):
    summary: str = Field(min_length=1, max_length=10000)
    work_ref: str = Field(min_length=1, max_length=500)
    test_results: dict[str, Any]
    leases: list[LeaseInput] = Field(min_length=1, max_length=100)


class PrepareWrite(LeaseInput):
    workspace_id: str
    resource: str
    expected_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    new_revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class FinishWrite(Input):
    permit_id: str
    observed_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    outcome: Literal["COMMIT", "ABORT", "RECONCILE"] = "COMMIT"


class ReportConflict(Input):
    category: Literal["OUT_OF_BAND", "REPOSITORY", "STUDIO_EDIT", "TASK", "INTERFACE"]
    resources: list[str] = Field(default_factory=list, max_length=100)
    agents: list[str] = Field(default_factory=list, max_length=100)
    description: str = Field(min_length=1, max_length=10000)
    task_id: str | None = None


class ResolveConflict(Input):
    conflict_id: str
    status: Literal["ACKNOWLEDGED", "RESOLVING", "RESOLVED", "DISMISSED"]
    reason: str = Field(min_length=1, max_length=10000)


OPERATIONS: dict[str, type[Input]] = {
    "get_project_state": Page,
    "list_agents": Page,
    "register_workspace": WorkspaceInput,
    "register_agent": RegisterAgent,
    "heartbeat": Heartbeat,
    "list_tasks": Page,
    "create_task": TaskInput,
    "claim_task": TaskRef,
    "update_task": TaskUpdate,
    "get_task_dependencies": TaskRef,
    "add_dependency": AddDependency,
    "list_locks": Page,
    "acquire_lock": Acquire,
    "renew_lock": Renew,
    "release_lock": LeaseInput,
    "check_lock": ResourceInput,
    "override_lock": Override,
    "send_message": SendMessage,
    "get_messages": Inbox,
    "acknowledge_message": MessageRef,
    "publish_interface": InterfaceInput,
    "get_interface": InterfaceRef,
    "propose_interface_change": InterfaceInput,
    "approve_interface": InterfaceApproval,
    "report_blocker": Blocker,
    "submit_work": Submit,
    "get_activity": Page,
    "check_conflicts": Page,
    "get_resource_owner": ResourceInput,
    "request_resource_transfer": SendMessage,
    "get_recent_changes": Page,
    "get_affected_agents": InterfaceRef,
    "get_integration_status": Page,
    "prepare_write": PrepareWrite,
    "finish_write": FinishWrite,
    "report_conflict": ReportConflict,
    "resolve_conflict": ResolveConflict,
}
