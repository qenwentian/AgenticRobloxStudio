import time

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .models import Agent, AgentSession, Event, Membership, Project, Workspace


class Rejected(Exception):
    def __init__(self, code: str, message: str, details: dict | None = None, status: int = 409):
        self.code, self.message, self.details, self.status = code, message, details or {}, status
        super().__init__(message)


def record(row, exclude: tuple = ()) -> dict:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns if c.name not in exclude}


class Core:
    def __init__(self, db: Session, project_id: str, user_id: str, session_ttl: int = 90):
        self.db, self.project_id, self.user_id = db, project_id, user_id
        self.session_ttl = session_ttl
        # ponytail: serialize per project; shard lock domains when measured throughput requires it.
        project = db.execute(
            update(Project)
            .where(Project.id == project_id)
            .values(generation=Project.generation + 1)
            .returning(Project)
        ).scalar_one_or_none()
        member = db.get(Membership, (project_id, user_id))
        if not project or not member:
            raise Rejected("FORBIDDEN", "Project membership required", status=403)
        self.project, self.role = project, member.role

    def writable(self):
        if self.role == "VIEWER":
            raise Rejected("FORBIDDEN", "Viewer access is read-only", status=403)

    def owner(self):
        if self.role != "OWNER":
            raise Rejected("FORBIDDEN", "Project owner required", status=403)

    def event(self, kind: str, payload: dict):
        self.db.add(
            Event(
                project_id=self.project_id, actor_id=self.user_id, event_type=kind, payload=payload
            )
        )

    def scoped(self, model, entity_id: str):
        row = self.db.get(model, entity_id)
        if not row or row.project_id != self.project_id:
            raise Rejected(
                "NOT_FOUND", "Entity is outside this project or does not exist", status=404
            )
        return row

    def session(self, session_id: str, active: bool = True):
        row = self.db.get(AgentSession, session_id)
        if not row:
            raise Rejected("NOT_FOUND", "Agent session not found", status=404)
        agent = self.scoped(Agent, row.agent_id)
        if agent.user_id != self.user_id:
            raise Rejected("FORBIDDEN", "Session belongs to another developer", status=403)
        if active and row.expires_at <= time.time():
            raise Rejected("SESSION_EXPIRED", "Register a new agent session")
        return row

    def agents(self):
        return self.db.scalars(select(Agent).where(Agent.project_id == self.project_id)).all()

    def expire(self):
        now = time.time()
        ids = [a.id for a in self.agents()]
        for session in self.db.scalars(
            select(AgentSession).where(
                AgentSession.agent_id.in_(ids),
                AgentSession.expires_at <= now,
                AgentSession.status != "OFFLINE",
            )
        ):
            session.status = "OFFLINE"
            self.event(
                "AGENT_DISCONNECTED", {"session_id": session.id, "agent_id": session.agent_id}
            )

    def register_workspace(self, payload):
        self.writable()
        if payload.mode != self.project.mode:
            raise Rejected("MODE_MISMATCH", "Workspace mode must match project mode")
        row = Workspace(project_id=self.project_id, user_id=self.user_id, **payload.model_dump())
        self.db.add(row)
        self.db.flush()
        self.event("WORKSPACE_REGISTERED", {"workspace_id": row.id})
        return {"workspace": record(row)}

    def register_agent(self, payload):
        self.writable()
        workspace = self.scoped(Workspace, payload.workspace_id)
        if workspace.user_id != self.user_id:
            raise Rejected("FORBIDDEN", "Workspace belongs to another developer", status=403)
        agent = self.db.scalar(
            select(Agent).where(
                Agent.project_id == self.project_id,
                Agent.user_id == self.user_id,
                Agent.name == payload.display_name,
            )
        )
        if not agent:
            agent = Agent(
                project_id=self.project_id,
                user_id=self.user_id,
                name=payload.display_name,
                capabilities=payload.capabilities,
            )
            self.db.add(agent)
            self.db.flush()
        session = AgentSession(
            agent_id=agent.id, workspace_id=workspace.id, expires_at=time.time() + self.session_ttl
        )
        self.db.add(session)
        self.db.flush()
        self.event("AGENT_CONNECTED", {"agent_id": agent.id, "session_id": session.id})
        return {"agent": record(agent), "session": record(session)}

    def heartbeat(self, payload):
        self.writable()
        row = self.session(payload.session_id)
        row.last_heartbeat = time.time()
        row.expires_at = time.time() + self.session_ttl
        row.status = payload.status
        from .models import Lease

        for lease in self.db.scalars(
            select(Lease).where(
                Lease.session_id == row.id,
                Lease.released_at.is_(None),
                Lease.expires_at > time.time(),
            )
        ):
            lease.expires_at = time.time() + 60
        return {"session": record(row)}

    def list_agents(self, payload):
        self.expire()
        rows = []
        for agent in self.agents():
            sessions = self.db.scalars(
                select(AgentSession)
                .where(AgentSession.agent_id == agent.id)
                .order_by(AgentSession.last_heartbeat.desc())
            ).all()
            rows.append({**record(agent), "sessions": [record(s) for s in sessions]})
        return {"agents": rows[payload.cursor : payload.cursor + payload.limit]}

    def execute(self, operation: str, payload):
        self.expire()
        return getattr(self, operation)(payload)
