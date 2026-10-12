import time

from sqlalchemy import or_, select
from studiosync_shared.schemas import Page

from .core import Core, Rejected, record
from .models import (
    Agent,
    AgentSession,
    Conflict,
    Consumer,
    Contract,
    ContractVersion,
    Event,
    Message,
    Receipt,
    Submission,
    Task,
    Workspace,
    WritePermit,
)
from .resources import covers, overlaps


class Collaboration(Core):
    def message_visible(self, row):
        if row.recipient_agent_id is None:
            return True
        recipient = self.db.get(Agent, row.recipient_agent_id)
        sender_session = self.db.get(AgentSession, row.sender_session_id)
        sender = self.db.get(Agent, sender_session.agent_id)
        return self.user_id in {recipient.user_id, sender.user_id}

    def send_message(self, payload):
        self.writable()
        session = self.session(payload.session_id)
        if payload.recipient_agent_id:
            self.scoped(Agent, payload.recipient_agent_id)
        if payload.related_task_id:
            self.scoped(Task, payload.related_task_id)
        if payload.reply_to:
            parent = self.scoped(Message, payload.reply_to)
            if not self.message_visible(parent):
                raise Rejected(
                    "FORBIDDEN", "Cannot reply to another developer's private thread", status=403
                )
        values = payload.model_dump(exclude={"session_id", "related_resource"})
        row = Message(
            project_id=self.project_id,
            sender_session_id=session.id,
            **values,
            related_resource=self.resource_key(payload.related_resource)
            if payload.related_resource
            else None,
        )
        self.db.add(row)
        self.db.flush()
        self.event(
            "MESSAGE_CREATED",
            {
                "message_id": row.id,
                "recipient_agent_id": row.recipient_agent_id,
                "sender_agent_id": session.agent_id,
                "message_type": row.message_type,
            },
        )
        return {"message": record(row)}

    request_resource_transfer = send_message

    def get_messages(self, payload):
        session = self.session(payload.session_id)
        rows = self.db.scalars(
            select(Message)
            .where(
                Message.project_id == self.project_id,
                or_(
                    Message.recipient_agent_id.is_(None),
                    Message.recipient_agent_id == session.agent_id,
                    Message.sender_session_id == session.id,
                ),
            )
            .order_by(Message.created_at, Message.id)
        ).all()
        return {
            "messages": [
                {**record(m), "acknowledged": bool(self.db.get(Receipt, (m.id, session.id)))}
                for m in rows[payload.cursor : payload.cursor + payload.limit]
            ]
        }

    def acknowledge_message(self, payload):
        self.writable()
        session = self.session(payload.session_id)
        message = self.scoped(Message, payload.message_id)
        if message.recipient_agent_id not in {None, session.agent_id}:
            raise Rejected("FORBIDDEN", "Only a recipient may acknowledge this message", status=403)
        self.db.merge(Receipt(message_id=message.id, session_id=session.id))
        self.event("MESSAGE_ACKNOWLEDGED", {"message_id": message.id, "agent_id": session.agent_id})
        return {"acknowledged": True}

    def contract(self, name):
        row = self.db.scalar(
            select(Contract).where(Contract.project_id == self.project_id, Contract.name == name)
        )
        if not row:
            raise Rejected("NOT_FOUND", "Interface contract not found", status=404)
        return row

    def versions(self, contract):
        return self.db.scalars(
            select(ContractVersion)
            .where(ContractVersion.contract_id == contract.id)
            .order_by(ContractVersion.version)
        ).all()

    def consumers(self, contract):
        return self.db.scalars(select(Consumer).where(Consumer.contract_id == contract.id)).all()

    def publish_interface(self, payload):
        self.writable()
        session = self.session(payload.session_id)
        if self.db.scalar(
            select(Contract).where(
                Contract.project_id == self.project_id, Contract.name == payload.name
            )
        ):
            raise Rejected(
                "CONTRACT_EXISTS", "Use propose_interface_change to create a new version"
            )
        contract = Contract(
            project_id=self.project_id, name=payload.name, owner_agent_id=session.agent_id
        )
        self.db.add(contract)
        self.db.flush()
        version = ContractVersion(
            contract_id=contract.id,
            version=1,
            **payload.model_dump(exclude={"session_id", "name", "consumers"}),
            approval_status="APPROVED",
        )
        self.db.add(version)
        for agent_id in set(payload.consumers):
            self.scoped(Agent, agent_id)
            self.db.add(Consumer(contract_id=contract.id, agent_id=agent_id, approved_version=0))
        self.event(
            "INTERFACE_PUBLISHED",
            {
                "contract_id": contract.id,
                "name": contract.name,
                "version": 1,
                "consumers": payload.consumers,
            },
        )
        self.db.flush()
        return {"contract": record(contract), "version": record(version)}

    def get_interface(self, payload):
        contract = self.contract(payload.name)
        return {
            "contract": record(contract),
            "versions": [record(v) for v in self.versions(contract)],
            "consumers": [record(c) for c in self.consumers(contract)],
        }

    def propose_interface_change(self, payload):
        self.writable()
        self.session(payload.session_id)
        contract = self.contract(payload.name)
        versions = self.versions(contract)
        row = ContractVersion(
            contract_id=contract.id,
            version=versions[-1].version + 1,
            **payload.model_dump(exclude={"session_id", "name", "consumers"}),
        )
        self.db.add(row)
        for agent_id in set(payload.consumers):
            self.scoped(Agent, agent_id)
            if not self.db.get(Consumer, (contract.id, agent_id)):
                self.db.add(
                    Consumer(contract_id=contract.id, agent_id=agent_id, approved_version=0)
                )
        self.db.flush()
        affected = [c.agent_id for c in self.consumers(contract)]
        self.add_conflict(
            "INTERFACE",
            [self.resource_key("interface:" + contract.name)],
            affected,
            f"Contract {contract.name} version {row.version} awaits compatibility approval",
        )
        self.event(
            "INTERFACE_UPDATED",
            {
                "name": contract.name,
                "version": row.version,
                "consumers": affected,
                "approval_required": True,
            },
        )
        return {"version": record(row), "affected_agents": affected}

    def approve_interface(self, payload):
        self.writable()
        session = self.session(payload.session_id)
        contract = self.contract(payload.name)
        version = self.db.get(ContractVersion, (contract.id, payload.version))
        if not version:
            raise Rejected("NOT_FOUND", "Contract version not found", status=404)
        latest = self.versions(contract)[-1]
        if payload.version != latest.version:
            raise Rejected("STALE_VERSION", "Approve the latest contract version")
        consumer = self.db.get(Consumer, (contract.id, session.agent_id))
        if session.agent_id == contract.owner_agent_id:
            version.approval_status = "APPROVED"
        elif consumer:
            if version.approval_status != "APPROVED":
                raise Rejected("OWNER_APPROVAL", "Contract owner must approve the proposal first")
            consumer.approved_version = payload.version
        else:
            raise Rejected(
                "FORBIDDEN", "Only owner and registered consumers can approve", status=403
            )
        self.db.flush()
        if version.approval_status == "APPROVED" and all(
            c.approved_version == version.version for c in self.consumers(contract)
        ):
            for conflict in self.db.scalars(
                select(Conflict).where(
                    Conflict.project_id == self.project_id,
                    Conflict.category == "INTERFACE",
                    Conflict.status.in_(["OPEN", "ACKNOWLEDGED", "RESOLVING"]),
                )
            ):
                if self.resource_key("interface:" + contract.name) in conflict.resources:
                    conflict.status = "RESOLVED"
                    conflict.history = conflict.history + [
                        {
                            "actor": self.user_id,
                            "reason": "All consumers confirmed latest version",
                            "at": time.time(),
                        }
                    ]
                    self.event(
                        "CONFLICT_RESOLVED",
                        {"conflict_id": conflict.id, "version": version.version},
                    )
        self.event(
            "INTERFACE_APPROVED",
            {"name": contract.name, "version": version.version, "agent_id": session.agent_id},
        )
        return {"version": record(version), "consumer": record(consumer) if consumer else None}

    def get_affected_agents(self, payload):
        contract = self.contract(payload.name)
        return {
            "agents": [record(self.scoped(Agent, c.agent_id)) for c in self.consumers(contract)]
        }

    def report_blocker(self, payload):
        self.writable()
        session = self.session(payload.session_id)
        task = self.scoped(Task, payload.task_id)
        if task.assigned_agent_id != session.agent_id or task.status in {"COMPLETED", "CANCELLED"}:
            raise Rejected("TASK_OWNERSHIP", "Only the active task owner can report its blocker")
        task.status = "BLOCKED"
        task.blocker_reason = payload.reason
        task.updated_at = time.time()
        self.event(
            "TASK_UPDATED", {"task_id": task.id, "status": "BLOCKED", "reason": payload.reason}
        )
        return {"task": record(task)}

    def get_activity(self, payload):
        rows = self.db.scalars(
            select(Event)
            .where(Event.project_id == self.project_id, Event.id > payload.cursor)
            .order_by(Event.id)
            .limit(payload.limit)
        ).all()
        # Events contain metadata only. Direct message body access remains recipient scoped.
        return {
            "events": [record(e) for e in rows],
            "cursor": rows[-1].id if rows else payload.cursor,
        }

    get_recent_changes = get_activity

    def prepare_write(self, payload):
        self.writable()
        session = self.session(payload.session_id)
        workspace = self.scoped(Workspace, payload.workspace_id)
        if workspace.user_id != self.user_id or session.workspace_id != workspace.id:
            raise Rejected(
                "FORBIDDEN", "Write is outside the session's registered workspace", status=403
            )
        lease, reserved = self.validate_lease(payload)
        key = self.resource_key(payload.resource)
        if not key.startswith("file:") or lease.mode != "WRITE" or not covers(reserved.key, key):
            raise Rejected("WRITE_OWNERSHIP", "An exclusive covering file reservation is required")
        if lease.task_id:
            task = self.scoped(Task, lease.task_id)
            if task.assigned_agent_id != session.agent_id or task.status not in {
                "CLAIMED",
                "IN_PROGRESS",
                "BLOCKED",
            }:
                raise Rejected("TASK_OWNERSHIP", "Task ownership no longer authorizes this write")
        resource = self.resource(key)
        if any(overlaps(key, r.key) for _, r in self.pending(key)):
            raise Rejected("WRITE_PENDING", "Another write is pending for this resource")
        if resource.revision is not None and resource.revision != payload.expected_revision:
            raise Rejected(
                "REVISION_MISMATCH",
                "Expected revision differs from central metadata",
                {"revision": resource.revision},
            )
        resource.revision = payload.expected_revision
        row = WritePermit(
            resource_id=resource.id,
            lease_id=lease.id,
            workspace_id=workspace.id,
            expected_revision=payload.expected_revision,
            new_revision=payload.new_revision,
        )
        self.db.add(row)
        self.db.flush()
        self.event(
            "WRITE_PREPARED",
            {
                "permit_id": row.id,
                "resource": key,
                "workspace_id": workspace.id,
                "generation": lease.generation,
            },
        )
        return {"permit": record(row)}

    def finish_write(self, payload):
        self.writable()
        permit = self.db.get(WritePermit, payload.permit_id)
        if not permit:
            raise Rejected("NOT_FOUND", "Write permit not found", status=404)
        resource = self.scoped_resource_for_permit(permit)
        workspace = self.scoped(Workspace, permit.workspace_id)
        if payload.outcome == "RECONCILE":
            self.owner()
        elif workspace.user_id != self.user_id:
            raise Rejected("FORBIDDEN", "Write belongs to another developer", status=403)
        if permit.status != "PENDING":
            expected = (
                permit.new_revision if permit.status == "COMMITTED" else permit.expected_revision
            )
            if payload.observed_revision != expected:
                raise Rejected("REVISION_MISMATCH", "Finished write cannot be changed")
            return {"permit": record(permit), "revision": resource.revision}
        if payload.outcome == "ABORT" and payload.observed_revision == permit.expected_revision:
            permit.status = "ABORTED"
        elif (
            payload.outcome in {"COMMIT", "RECONCILE"}
            and payload.observed_revision == permit.new_revision
        ):
            permit.status = "COMMITTED"
            resource.revision = permit.new_revision
        elif (
            payload.outcome == "RECONCILE" and payload.observed_revision == permit.expected_revision
        ):
            permit.status = "ABORTED"
        else:
            raise Rejected("REVISION_MISMATCH", "Observed hash does not match a safe outcome")
        self.event(
            "RESOURCE_CHANGED" if permit.status == "COMMITTED" else "WRITE_ABORTED",
            {
                "permit_id": permit.id,
                "resource": resource.key,
                "revision": resource.revision,
                "workspace_id": workspace.id,
                "reconciled": payload.outcome == "RECONCILE",
            },
        )
        return {"permit": record(permit), "revision": resource.revision}

    def scoped_resource_for_permit(self, permit):
        from .models import Resource

        return self.scoped(Resource, permit.resource_id)

    def validate_integration_task(self, task):
        if self.unfinished(task):
            raise Rejected("DEPENDENCY_BLOCKED", "Dependencies are unfinished")
        agent_id = task.assigned_agent_id
        for consumer, contract in self.db.execute(
            select(Consumer, Contract)
            .join(Contract)
            .where(Contract.project_id == self.project_id, Consumer.agent_id == agent_id)
        ):
            latest = self.versions(contract)[-1]
            if latest.approval_status != "APPROVED" or consumer.approved_version != latest.version:
                raise Rejected(
                    "INTERFACE_INCOMPATIBLE",
                    "Consumer must approve the current contract",
                    {"contract": contract.name, "version": latest.version},
                )
        for conflict in self.db.scalars(
            select(Conflict).where(
                Conflict.project_id == self.project_id,
                Conflict.status.in_(["OPEN", "ACKNOWLEDGED", "RESOLVING"]),
                Conflict.category != "RESOURCE",
            )
        ):
            if (
                conflict.task_id == task.id
                or agent_id in conflict.agents
                or any(overlaps(a, b) for a in task.resources for b in conflict.resources)
            ):
                raise Rejected(
                    "INTEGRATION_CONFLICT",
                    "Resolve affected conflicts before integration",
                    {"conflict_id": conflict.id},
                )
        if any(any(overlaps(r.key, k) for k in task.resources) for _, r in self.pending("")):
            raise Rejected("WRITE_PENDING", "Pending file writes prevent integration")

    def submit_work(self, payload):
        self.writable()
        session = self.session(payload.session_id)
        task = self.scoped(Task, payload.task_id)
        if task.assigned_agent_id != session.agent_id or task.status not in {
            "CLAIMED",
            "IN_PROGRESS",
        }:
            raise Rejected("TASK_OWNERSHIP", "Only the working task owner can submit")
        self.validate_integration_task(task)
        tests = payload.test_results
        if (
            tests.get("passed") is not True
            or tests.get("exit_code") != 0
            or not tests.get("command")
        ):
            raise Rejected(
                "TESTS_REQUIRED", "Include actual test command, zero exit code and passing result"
            )
        reservations = []
        for proof in payload.leases:
            if proof.session_id != session.id:
                raise Rejected(
                    "STALE_LEASE", "All submitted leases must belong to the submitting session"
                )
            lease, resource = self.validate_lease(proof)
            if lease.mode != "WRITE" or lease.task_id != task.id:
                raise Rejected(
                    "TASK_SCOPE", "Submission needs exclusive leases associated with this task"
                )
            reservations.append(resource.key)
        if not task.resources or not all(
            any(covers(r, key) for r in reservations) for key in task.resources
        ):
            raise Rejected("TASK_SCOPE", "All declared task resources need lease evidence")
        row = Submission(
            task_id=task.id,
            session_id=session.id,
            summary=payload.summary,
            work_ref=payload.work_ref,
            test_results=tests,
            resources=reservations,
        )
        self.db.add(row)
        self.db.flush()
        task.submission_id, task.status, task.updated_at = row.id, "IN_REVIEW", time.time()
        session.status = "REVIEWING"
        self.event(
            "WORK_SUBMITTED",
            {"task_id": task.id, "submission_id": row.id, "work_ref": row.work_ref, "tests": tests},
        )
        return {"submission": record(row), "task": record(task)}

    def get_integration_status(self, payload):
        rows = []
        for task in self.tasks()[payload.cursor : payload.cursor + payload.limit]:
            reason = None
            try:
                self.validate_integration_task(task)
                if task.status != "COMPLETED" or not task.submission_id:
                    reason = "Task is not completed with a submission"
            except Rejected as exc:
                reason = exc.message
            rows.append(
                {
                    "task_id": task.id,
                    "ready": reason is None,
                    "reason": reason,
                    "submission_id": task.submission_id,
                }
            )
        return {
            "integration": rows,
            "test_evidence": "Client-reported; central server does not execute source code",
        }

    def get_project_state(self, payload):
        p = Page(limit=payload.limit, cursor=payload.cursor)
        messages = self.db.scalars(
            select(Message)
            .where(Message.project_id == self.project_id)
            .order_by(Message.created_at.desc(), Message.id)
        ).all()
        contracts = self.db.scalars(
            select(Contract).where(Contract.project_id == self.project_id)
        ).all()
        return {
            "project": record(self.project),
            "developer_id": self.user_id,
            "role": self.role,
            **self.list_agents(p),
            **self.list_tasks(p),
            **self.list_locks(p),
            **self.check_conflicts(p),
            "messages": [record(m) for m in messages if self.message_visible(m)][: payload.limit],
            "interfaces": [
                {
                    **record(c),
                    "versions": [record(v) for v in self.versions(c)],
                    "consumers": [record(x) for x in self.consumers(c)],
                }
                for c in contracts
            ],
            "pending_writes": [
                {**record(permit), "resource": resource.key}
                for permit, resource in self.pending("")
            ],
            **self.get_integration_status(p),
            "timestamp": time.time(),
        }
