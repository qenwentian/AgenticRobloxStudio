import secrets
import time

from sqlalchemy import select
from studiosync_shared.schemas import Error, Result

from .auth import digest
from .collaboration import Collaboration
from .core import Rejected, record
from .models import Agent, AgentSession, Conflict, Dependency, Lease, Resource, Task, WritePermit
from .resources import canonical, covers, overlaps


class Coordination(Collaboration):
    def resource_key(self, value):
        try:
            return canonical(value)
        except ValueError as exc:
            raise Rejected("INVALID_RESOURCE", str(exc), status=422) from exc

    def resource(self, key):
        row = self.db.scalar(
            select(Resource).where(Resource.project_id == self.project_id, Resource.key == key)
        )
        if not row:
            row = Resource(project_id=self.project_id, key=key)
            self.db.add(row)
            self.db.flush()
        return row

    def expire(self):
        super().expire()
        for lease, resource in self.db.execute(
            select(Lease, Resource)
            .join(Resource)
            .where(
                Resource.project_id == self.project_id,
                Lease.released_at.is_(None),
                Lease.expires_at <= time.time(),
            )
        ):
            lease.released_at = time.time()
            self.event("RESOURCE_EXPIRED", {"lease_id": lease.id, "resource": resource.key})

    def active_leases(self):
        return self.db.execute(
            select(Lease, Resource)
            .join(Resource)
            .where(
                Resource.project_id == self.project_id,
                Lease.released_at.is_(None),
                Lease.expires_at > time.time(),
            )
        ).all()

    def lease_record(self, lease, resource):
        session = self.db.get(AgentSession, lease.session_id)
        return {
            **record(lease, ("token_hash",)),
            "resource": resource.key,
            "agent_id": session.agent_id,
            "revision": resource.revision,
        }

    def list_locks(self, payload):
        rows = [self.lease_record(lease, r) for lease, r in self.active_leases()]
        return {"locks": rows[payload.cursor : payload.cursor + payload.limit]}

    def check_lock(self, payload):
        key = self.resource_key(payload.resource)
        return {
            "resource": key,
            "locks": [
                self.lease_record(lease, r)
                for lease, r in self.active_leases()
                if overlaps(key, r.key)
            ],
        }

    get_resource_owner = check_lock

    def pending(self, key):
        return self.db.execute(
            select(WritePermit, Resource)
            .join(Resource)
            .where(Resource.project_id == self.project_id, WritePermit.status == "PENDING")
        ).all()

    def acquire_lock(self, payload):
        self.writable()
        session = self.session(payload.session_id)
        key = self.resource_key(payload.resource)
        if payload.task_id:
            task = self.scoped(Task, payload.task_id)
            if task.assigned_agent_id != session.agent_id or task.status not in {
                "CLAIMED",
                "IN_PROGRESS",
                "BLOCKED",
            }:
                raise Rejected(
                    "TASK_OWNERSHIP", "Claim the related task before acquiring resources"
                )
            if not any(covers(r, key) for r in task.resources):
                raise Rejected(
                    "TASK_SCOPE", "Requested resource is outside the declared task scope"
                )
        for permit, resource in self.pending(key):
            if overlaps(key, resource.key) and payload.mode != "INTENT":
                raise Rejected(
                    "WRITE_PENDING",
                    "An uncertain write must be reconciled before ownership transfers",
                    {"permit_id": permit.id, "resource": resource.key},
                )
        conflicts = [
            self.lease_record(lease, r)
            for lease, r in self.active_leases()
            if overlaps(key, r.key)
            and payload.mode != "INTENT"
            and lease.mode != "INTENT"
            and (payload.mode == "WRITE" or lease.mode == "WRITE")
        ]
        if conflicts:
            self.add_conflict(
                "RESOURCE",
                [key],
                [session.agent_id] + [lease["agent_id"] for lease in conflicts],
                "Reservation conflicts with active ownership",
                payload.task_id,
            )
            self.event(
                "LOCK_CONFLICT",
                {
                    "resource": key,
                    "requester": session.agent_id,
                    "owners": [lease["agent_id"] for lease in conflicts],
                },
            )
            return Result(
                ok=False,
                error=Error(
                    code="LOCK_CONFLICT",
                    message="Resource is reserved",
                    details={"locks": conflicts},
                ),
            )
        token = secrets.token_urlsafe(32)
        resource = self.resource(key)
        row = Lease(
            resource_id=resource.id,
            session_id=session.id,
            task_id=payload.task_id,
            mode=payload.mode,
            token_hash=digest(token),
            generation=self.project.generation,
            expires_at=time.time() + payload.ttl,
        )
        self.db.add(row)
        self.db.flush()
        self.event(
            "RESOURCE_LOCKED",
            {
                "resource": key,
                "lease_id": row.id,
                "agent_id": session.agent_id,
                "generation": row.generation,
                "mode": row.mode,
            },
        )
        return {"lease": {**self.lease_record(row, resource), "lease_token": token}}

    def validate_lease(self, payload, active=True):
        session = self.session(payload.session_id, active=active)
        lease = self.db.get(Lease, payload.lease_id)
        if not lease:
            raise Rejected("STALE_LEASE", "Lease does not exist")
        resource = self.scoped(Resource, lease.resource_id)
        if (
            lease.session_id != session.id
            or not secrets.compare_digest(lease.token_hash, digest(payload.lease_token))
            or lease.generation != payload.generation
        ):
            raise Rejected("STALE_LEASE", "Lease identity or fencing generation mismatch")
        if active and (lease.released_at or lease.expires_at <= time.time()):
            raise Rejected("STALE_LEASE", "Lease expired or was revoked")
        return lease, resource

    def renew_lock(self, payload):
        self.writable()
        lease, resource = self.validate_lease(payload)
        lease.expires_at = time.time() + payload.ttl
        self.event("RESOURCE_RENEWED", {"lease_id": lease.id})
        return {"lease": self.lease_record(lease, resource)}

    def release_lock(self, payload):
        self.writable()
        lease, resource = self.validate_lease(payload, active=False)
        if any(overlaps(resource.key, r.key) for _, r in self.pending(resource.key)):
            raise Rejected("WRITE_PENDING", "Finish or reconcile the pending write first")
        if not lease.released_at:
            lease.released_at = time.time()
            self.event("RESOURCE_RELEASED", {"lease_id": lease.id, "resource": resource.key})
        return {"released": True, "lease_id": lease.id}

    def override_lock(self, payload):
        self.writable()
        lease = self.db.get(Lease, payload.lease_id)
        if not lease:
            raise Rejected("NOT_FOUND", "Lease not found", status=404)
        if self.role != "OWNER" and not self.own_lease(lease):
            # A developer may always clear their own stuck reservation; dissolving
            # another developer's lease stays an owner decision with an audit reason.
            raise Rejected("FORBIDDEN", "Project owner required", status=403)
        resource = self.scoped(Resource, lease.resource_id)
        if any(overlaps(resource.key, r.key) for _, r in self.pending(resource.key)):
            raise Rejected("WRITE_PENDING", "Reconcile pending writes before overriding")
        lease.released_at = time.time()
        self.event(
            "RESOURCE_RELEASED",
            {
                "lease_id": lease.id,
                "override": True,
                "reason": payload.reason,
                "actor": self.user_id,
            },
        )
        return {"released": True}

    def tasks(self):
        return self.db.scalars(
            select(Task)
            .where(Task.project_id == self.project_id)
            .order_by(Task.created_at, Task.id)
        ).all()

    def list_tasks(self, payload):
        return {
            "tasks": [
                record(t) for t in self.tasks()[payload.cursor : payload.cursor + payload.limit]
            ]
        }

    def dependencies(self, task_id):
        return self.db.scalars(
            select(Task)
            .join(Dependency, Dependency.depends_on == Task.id)
            .where(Dependency.task_id == task_id)
        ).all()

    def unfinished(self, task):
        return [t.id for t in self.dependencies(task.id) if t.status != "COMPLETED"]

    def create_task(self, payload):
        self.writable()
        self.session(payload.session_id)
        if payload.assigned_agent_id:
            self.scoped(Agent, payload.assigned_agent_id)
        row = Task(
            project_id=self.project_id,
            creator_id=self.user_id,
            **payload.model_dump(exclude={"session_id", "dependencies", "resources"}),
            resources=[self.resource_key(r) for r in payload.resources],
        )
        self.db.add(row)
        self.db.flush()
        for dependency in set(payload.dependencies):
            self.scoped(Task, dependency)
            self.db.add(Dependency(task_id=row.id, depends_on=dependency))
        self.db.flush()
        if self.unfinished(row):
            row.status = "BLOCKED"
            row.blocker_reason = "dependencies"
            self.event(
                "DEPENDENCY_BLOCKED", {"task_id": row.id, "dependencies": self.unfinished(row)}
            )
        for other in self.tasks():
            if (
                other.id != row.id
                and other.status not in {"COMPLETED", "CANCELLED"}
                and any(overlaps(a, b) for a in row.resources for b in other.resources)
            ):
                self.add_conflict(
                    "TASK", row.resources, [], "Declared task scope overlaps " + other.id, row.id
                )
        self.event("TASK_CREATED", {"task_id": row.id})
        return {"task": record(row)}

    def claim_task(self, payload):
        self.writable()
        session = self.session(payload.session_id)
        task = self.scoped(Task, payload.task_id)
        if task.assigned_agent_id not in {None, session.agent_id}:
            raise Rejected("TASK_OWNERSHIP", "Task is assigned to another agent")
        if self.unfinished(task):
            raise Rejected(
                "DEPENDENCY_BLOCKED",
                "Task has unfinished dependencies",
                {"tasks": self.unfinished(task)},
            )
        if task.status not in {"READY", "BACKLOG", "CLAIMED"}:
            raise Rejected("TASK_TRANSITION", "Task is not claimable")
        task.assigned_agent_id = session.agent_id
        task.status, task.updated_at = "CLAIMED", time.time()
        session.current_task_id, session.status = task.id, "WORKING"
        self.event("TASK_CLAIMED", {"task_id": task.id, "agent_id": session.agent_id})
        return {"task": record(task)}

    def update_task(self, payload):
        self.writable()
        session = self.session(payload.session_id)
        task = self.scoped(Task, payload.task_id)
        if (
            payload.assigned_agent_id is not None
            and payload.assigned_agent_id != task.assigned_agent_id
        ):
            self.owner()
            self.scoped(Agent, payload.assigned_agent_id)
            if not payload.reason:
                raise Rejected("REASON_REQUIRED", "Reassignment requires an audit reason")
            if any(lease.task_id == task.id for lease, _ in self.active_leases()):
                raise Rejected("ACTIVE_LEASE", "Release or revoke task leases before reassignment")
            task.assigned_agent_id = payload.assigned_agent_id
            task.submission_id = None
            if payload.status != "READY" or task.status in {"COMPLETED", "CANCELLED"}:
                raise Rejected(
                    "TASK_TRANSITION", "Reassignment must return unfinished work to READY"
                )
            task.status = "READY"
        elif task.assigned_agent_id != session.agent_id and self.role != "OWNER":
            raise Rejected("TASK_OWNERSHIP", "Task is owned by another agent")
        transitions = {
            "BACKLOG": {"READY", "CANCELLED"},
            "READY": {"BACKLOG", "CANCELLED"},
            "CLAIMED": {"IN_PROGRESS", "BLOCKED", "READY", "CANCELLED"},
            "IN_PROGRESS": {"BLOCKED", "CANCELLED"},
            "BLOCKED": {"READY", "IN_PROGRESS", "CANCELLED"},
            "IN_REVIEW": {"COMPLETED", "IN_PROGRESS", "BLOCKED"},
            "COMPLETED": set(),
            "CANCELLED": set(),
        }
        if payload.status != task.status and payload.status not in transitions[task.status]:
            raise Rejected("TASK_TRANSITION", "Invalid task status transition")
        if payload.status in {"READY", "IN_PROGRESS", "IN_REVIEW", "COMPLETED"} and self.unfinished(
            task
        ):
            raise Rejected("DEPENDENCY_BLOCKED", "Unfinished dependencies prevent this transition")
        if payload.status == "COMPLETED":
            if not task.submission_id:
                raise Rejected("SUBMISSION_REQUIRED", "Submit validated work before completion")
            self.validate_integration_task(task)
            task.completed_at = time.time()
            session.current_task_id, session.status = None, "ONLINE"
            for working in self.db.scalars(
                select(AgentSession).where(AgentSession.current_task_id == task.id)
            ):
                working.current_task_id, working.status = (
                    None,
                    "ONLINE" if working.expires_at > time.time() else "OFFLINE",
                )
        if payload.status == "BLOCKED":
            if not payload.reason:
                raise Rejected("REASON_REQUIRED", "Describe the blocker")
            task.blocker_reason = payload.reason
        else:
            task.blocker_reason = ""
        task.status, task.updated_at = payload.status, time.time()
        self.event(
            "TASK_COMPLETED" if task.status == "COMPLETED" else "TASK_UPDATED",
            {"task_id": task.id, "status": task.status, "reason": payload.reason},
        )
        if task.status == "COMPLETED":
            self.db.flush()
            for child in self.tasks():
                if (
                    child.status == "BLOCKED"
                    and child.blocker_reason == "dependencies"
                    and not self.unfinished(child)
                    and any(d.id == task.id for d in self.dependencies(child.id))
                ):
                    child.status = "READY"
                    child.blocker_reason = ""
                    self.event("DEPENDENCY_RESOLVED", {"task_id": child.id, "dependency": task.id})
        return {"task": record(task)}

    def add_dependency(self, payload):
        self.writable()
        session = self.session(payload.session_id)
        task = self.scoped(Task, payload.task_id)
        self.scoped(Task, payload.depends_on)
        if task.assigned_agent_id not in {None, session.agent_id} and self.role != "OWNER":
            raise Rejected("TASK_OWNERSHIP", "Cannot change another agent's dependency graph")
        if task.status in {"COMPLETED", "CANCELLED", "IN_REVIEW"}:
            raise Rejected(
                "TASK_TRANSITION", "Cannot add dependencies to reviewed or finished work"
            )
        seen, queue = set(), [payload.depends_on]
        while queue:
            node = queue.pop()
            if node == task.id:
                raise Rejected("DEPENDENCY_CYCLE", "Task dependencies must be acyclic")
            if node not in seen:
                seen.add(node)
                queue.extend(d.id for d in self.dependencies(node))
        self.db.merge(Dependency(task_id=task.id, depends_on=payload.depends_on))
        self.db.flush()
        if self.unfinished(task):
            task.status = "BLOCKED"
            task.blocker_reason = "dependencies"
            self.event(
                "DEPENDENCY_BLOCKED", {"task_id": task.id, "dependencies": self.unfinished(task)}
            )
        return {"dependencies": [record(d) for d in self.dependencies(task.id)]}

    def get_task_dependencies(self, payload):
        self.session(payload.session_id)
        self.scoped(Task, payload.task_id)
        return {"dependencies": [record(d) for d in self.dependencies(payload.task_id)]}

    def own_lease(self, lease: Lease) -> bool:
        session = self.db.get(AgentSession, lease.session_id)
        agent = self.db.get(Agent, session.agent_id) if session else None
        return bool(agent and agent.user_id == self.user_id)

    def own_agent(self, agent_id: str) -> bool:
        agent = self.db.get(Agent, agent_id)
        return bool(agent and agent.user_id == self.user_id)

    def may_resolve_conflict(self, row: Conflict) -> bool:
        # The owner arbitrates everything; everyone else only closes conflicts they
        # reported or that list one of their own agents.
        return (
            self.role == "OWNER"
            or row.reported_by == self.user_id
            or any(self.own_agent(agent_id) for agent_id in (row.agents or []))
        )

    def add_conflict(self, category, resources, agents, description, task_id=None):
        existing = self.db.scalar(
            select(Conflict).where(
                Conflict.project_id == self.project_id,
                Conflict.category == category,
                Conflict.description == description,
                Conflict.status.in_(["OPEN", "ACKNOWLEDGED", "RESOLVING"]),
            )
        )
        if existing and existing.resources == resources and existing.agents == agents:
            return existing
        row = Conflict(
            project_id=self.project_id,
            category=category,
            resources=resources,
            agents=agents,
            description=description,
            task_id=task_id,
            reported_by=self.user_id,
        )
        self.db.add(row)
        self.db.flush()
        self.event(
            "CONFLICT_DETECTED",
            {"conflict_id": row.id, "category": category, "resources": resources},
        )
        return row

    def check_conflicts(self, payload):
        rows = self.db.scalars(
            select(Conflict)
            .where(Conflict.project_id == self.project_id)
            .order_by(Conflict.created_at, Conflict.id)
        ).all()
        return {
            "conflicts": [record(c) for c in rows[payload.cursor : payload.cursor + payload.limit]]
        }

    def report_conflict(self, payload):
        self.writable()
        for agent in payload.agents:
            self.scoped(Agent, agent)
        if payload.task_id:
            self.scoped(Task, payload.task_id)
        row = self.add_conflict(
            payload.category,
            [self.resource_key(r) for r in payload.resources],
            payload.agents,
            payload.description,
            payload.task_id,
        )
        return {"conflict": record(row)}

    def resolve_conflict(self, payload):
        self.writable()
        row = self.scoped(Conflict, payload.conflict_id)
        if not self.may_resolve_conflict(row):
            raise Rejected(
                "FORBIDDEN", "Project owner, reporter or an involved developer required", 403
            )
        row.status = payload.status
        row.history = row.history + [
            {
                "actor": self.user_id,
                "status": payload.status,
                "reason": payload.reason,
                "at": time.time(),
            }
        ]
        self.event(
            "CONFLICT_RESOLVED"
            if payload.status in {"RESOLVED", "DISMISSED"}
            else "CONFLICT_UPDATED",
            {"conflict_id": row.id, "status": payload.status, "reason": payload.reason},
        )
        return {"conflict": record(row)}
