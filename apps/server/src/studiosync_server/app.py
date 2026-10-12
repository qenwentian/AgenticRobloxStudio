import asyncio
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select, text
from sqlalchemy.exc import SQLAlchemyError
from studiosync_shared.schemas import OPERATIONS, Account, MemberInput, ProjectInput, Result

from .auth import digest, issue_token, password_hash, verify_password
from .core import Core, Rejected, record
from .database import database
from .models import Credential, Event, Membership, Project, User


def create_app(
    database_url: str | None = None, bootstrap_key: str | None = None, core_type=Core
) -> FastAPI:
    engine, sessions = database(database_url)
    key = bootstrap_key or os.getenv("STUDIOSYNC_BOOTSTRAP_KEY", "")
    ttl = int(os.getenv("STUDIOSYNC_SESSION_TTL", "90"))

    @asynccontextmanager
    async def lifespan(_):
        async def housekeeping():
            while True:
                await asyncio.sleep(10)

                def sweep():
                    with sessions.begin() as db:
                        for project in db.scalars(select(Project)).all():
                            owner = db.scalar(
                                select(Membership).where(
                                    Membership.project_id == project.id, Membership.role == "OWNER"
                                )
                            )
                            core = core_type(db, project.id, owner.user_id, ttl)
                            core.expire()
                        db.execute(delete(Credential).where(Credential.expires_at < time.time()))
                        days = int(os.getenv("STUDIOSYNC_EVENT_RETENTION_DAYS", "90"))
                        db.execute(
                            delete(Event).where(Event.created_at < time.time() - days * 86400)
                        )

                try:
                    await asyncio.to_thread(sweep)
                except SQLAlchemyError:
                    pass  # /health surfaces database outages; requests fail closed.

        task = asyncio.create_task(housekeeping())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        engine.dispose()

    app = FastAPI(title="StudioSync AI", version="0.2.0", lifespan=lifespan)
    app.state.engine, app.state.sessions = engine, sessions
    buckets = defaultdict(deque)

    @app.middleware("http")
    async def limits(request: Request, call_next):
        # ponytail: bounded process-local limiter; reverse proxy enforces team-wide limits.
        now = time.monotonic()
        identity = (
            digest(request.headers.get("authorization", ""))
            if request.headers.get("authorization")
            else (request.client.host if request.client else "unknown")
        )
        if len(buckets) >= 10000 and identity not in buckets:
            buckets.clear()
        bucket = buckets[identity]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        ceiling = 30 if request.url.path.startswith("/auth/") else 600
        if len(bucket) >= ceiling:
            return JSONResponse(
                status_code=429,
                content={
                    "ok": False,
                    "data": {},
                    "error": {
                        "code": "RATE_LIMIT",
                        "message": "Retry after one minute",
                        "details": {},
                    },
                },
            )
        bucket.append(now)
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            length = 2_000_001
        if length > 2_000_000:
            return JSONResponse(
                status_code=413,
                content={
                    "ok": False,
                    "data": {},
                    "error": {
                        "code": "TOO_LARGE",
                        "message": "Metadata body exceeds limit",
                        "details": {},
                    },
                },
            )
        return await call_next(request)

    @app.exception_handler(Rejected)
    async def rejected(_, exc):
        return JSONResponse(
            status_code=exc.status,
            content={
                "ok": False,
                "data": {},
                "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation(_, exc):
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "data": {},
                "error": {
                    "code": "VALIDATION",
                    "message": "Invalid request input",
                    "details": {
                        "fields": [{"loc": list(e["loc"]), "type": e["type"]} for e in exc.errors()]
                    },
                },
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_, exc):
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "data": {},
                "error": {
                    "code": "DATABASE_UNAVAILABLE",
                    "message": "Database operation failed; retry safely",
                    "details": {},
                },
            },
        )

    def authenticated(authorization: str = Header(default="")) -> str:
        if not authorization.startswith("Bearer "):
            raise Rejected("UNAUTHENTICATED", "Bearer credential required", status=401)
        with sessions() as db:
            credential = db.get(Credential, digest(authorization[7:]))
            if not credential or credential.expires_at <= time.time():
                raise Rejected("UNAUTHENTICATED", "Credential expired or invalid", status=401)
            return credential.user_id

    @app.get("/health")
    def health():
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok", "database": engine.dialect.name}

    @app.post("/auth/register")
    def register(payload: Account, x_bootstrap_key: str = Header(default="")):
        if not key or not secrets.compare_digest(key, x_bootstrap_key):
            raise Rejected("FORBIDDEN", "Administrator bootstrap credential required", status=403)
        with sessions.begin() as db:
            if db.scalar(select(User).where(User.name == payload.name)):
                raise Rejected("DUPLICATE", "Developer name already exists")
            user = User(name=payload.name, password_hash=password_hash(payload.password))
            db.add(user)
            db.flush()
            return issue_token(db, user)

    @app.post("/auth/login")
    def login(payload: Account):
        with sessions.begin() as db:
            user = db.scalar(select(User).where(User.name == payload.name))
            if not user or not verify_password(payload.password, user.password_hash):
                raise Rejected("UNAUTHENTICATED", "Invalid login", status=401)
            return issue_token(db, user)

    @app.get("/projects")
    def projects(user_id: str = Depends(authenticated)):
        with sessions() as db:
            rows = db.scalars(select(Project).join(Membership).where(Membership.user_id == user_id))
            return {"projects": [record(p) for p in rows]}

    @app.post("/projects")
    def create_project(payload: ProjectInput, user_id: str = Depends(authenticated)):
        with sessions.begin() as db:
            row = Project(**payload.model_dump())
            db.add(row)
            db.flush()
            db.add(Membership(project_id=row.id, user_id=user_id, role="OWNER"))
            db.add(
                Event(project_id=row.id, actor_id=user_id, event_type="PROJECT_CREATED", payload={})
            )
            return {"project": record(row)}

    @app.post("/projects/{project_id}/members")
    def add_member(project_id: str, payload: MemberInput, user_id: str = Depends(authenticated)):
        with sessions.begin() as db:
            core = core_type(db, project_id, user_id, ttl)
            core.owner()
            if not db.get(User, payload.user_id):
                raise Rejected("NOT_FOUND", "Developer not found", status=404)
            current = db.get(Membership, (project_id, payload.user_id))
            if current and current.role == "OWNER" and payload.role != "OWNER":
                owners = db.scalars(
                    select(Membership).where(
                        Membership.project_id == project_id, Membership.role == "OWNER"
                    )
                ).all()
                if len(owners) == 1:
                    raise Rejected("LAST_OWNER", "Project must retain an owner")
            db.merge(Membership(project_id=project_id, **payload.model_dump()))
            core.event("MEMBERSHIP_UPDATED", payload.model_dump())
            return {"membership": payload.model_dump()}

    def add_action(operation, schema):
        def action(project_id: str, payload, user_id: str = Depends(authenticated)) -> Result:
            with sessions.begin() as db:
                core = core_type(db, project_id, user_id, ttl)
                result = core.execute(operation, payload)
                if isinstance(result, Result):
                    return result
                return Result(data=result)

        action.__annotations__["payload"] = schema
        action.__name__ = operation
        app.post("/projects/{project_id}/actions/" + operation, response_model=Result)(action)

    @app.delete("/projects/{project_id}/members/{member_id}")
    def remove_member(project_id: str, member_id: str, user_id: str = Depends(authenticated)):
        from .models import Agent, AgentSession, Lease, Resource

        with sessions.begin() as db:
            core = core_type(db, project_id, user_id, ttl)
            core.owner()
            member = db.get(Membership, (project_id, member_id))
            if not member:
                return {"removed": True}
            owners = db.scalars(
                select(Membership).where(
                    Membership.project_id == project_id, Membership.role == "OWNER"
                )
            ).all()
            if member.role == "OWNER" and len(owners) == 1:
                raise Rejected("LAST_OWNER", "Project must retain an owner")
            agent_ids = db.scalars(
                select(Agent.id).where(Agent.project_id == project_id, Agent.user_id == member_id)
            ).all()
            session_ids = db.scalars(
                select(AgentSession.id).where(AgentSession.agent_id.in_(agent_ids))
            ).all()
            for session_id in session_ids:
                session = db.get(AgentSession, session_id)
                session.status, session.expires_at = "OFFLINE", time.time()
                core.event(
                    "AGENT_DISCONNECTED", {"session_id": session_id, "reason": "Membership removed"}
                )
            for lease in db.scalars(
                select(Lease).where(Lease.session_id.in_(session_ids), Lease.released_at.is_(None))
            ):
                lease.released_at = time.time()
                resource = db.get(Resource, lease.resource_id)
                core.event(
                    "RESOURCE_RELEASED",
                    {
                        "lease_id": lease.id,
                        "resource": resource.key,
                        "reason": "Membership removed",
                        "override": True,
                    },
                )
            db.delete(member)
            core.event("MEMBERSHIP_REMOVED", {"user_id": member_id})
            return {"removed": True}

    for operation, schema in OPERATIONS.items():
        add_action(operation, schema)

    @app.websocket("/projects/{project_id}/events")
    async def stream(websocket: WebSocket, project_id: str):
        try:
            user_id = authenticated(websocket.headers.get("authorization", ""))
            with sessions.begin() as db:
                core_type(db, project_id, user_id, ttl)
        except Rejected:
            await websocket.close(code=1008)
            return
        await websocket.accept()
        cursor = 0
        try:
            start = await asyncio.wait_for(websocket.receive_json(), 15)
            cursor = max(0, int(start.get("cursor", 0)))
            while True:
                user_id = authenticated(websocket.headers.get("authorization", ""))

                def fetch(user_id=user_id, cursor=cursor):
                    from studiosync_shared.schemas import Page

                    with sessions.begin() as db:
                        return core_type(db, project_id, user_id, ttl).get_activity(
                            Page(cursor=cursor)
                        )

                batch = await asyncio.to_thread(fetch)
                cursor = batch.get("cursor", cursor)
                await websocket.send_json(batch)
                await asyncio.sleep(2)
        except (WebSocketDisconnect, Rejected, TimeoutError, ValueError, SQLAlchemyError):
            await websocket.close()

    return app


def production_app():
    from .coordination import Coordination

    return create_app(core_type=Coordination)
