import asyncio
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, Field
from studiosync_shared.client import Backend
from studiosync_shared.config import backend_token, load_config
from studiosync_shared.resources import canonical
from studiosync_shared.schemas import OPERATIONS, Error, ResourceInput, Result

from .ledger import Ledger
from .watcher import Watcher
from .workspace import Workspace, atomic_replace, revision


class WriteRequest(OPERATIONS["prepare_write"]):
    content: str = Field(max_length=1_000_000)


class PairRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=10, max_length=100)


def failure(code, message, status=409, details=None):
    return JSONResponse(
        status_code=status,
        content=Result(
            ok=False, error=Error(code=code, message=message, details=details or {})
        ).model_dump(),
    )


def create_bridge(config=None, backend=None, pair_code=None, watch=True):
    config = config or load_config()
    backend = backend or Backend(config.server_url, backend_token(), config.project_id)
    workspace = Workspace(config.workspace_root)
    ledger = Ledger(workspace.root)
    watcher = Watcher(workspace, backend, ledger, config)
    write_lock = FileLock(str(workspace.root / ".studiosync" / "workspace.lock"), timeout=10)
    pairing = {
        "code": pair_code or secrets.token_urlsafe(16),
        "expires": time.time() + 300,
        "attempts": 0,
        # Only unused codes expire. A token entry of None means it lasts for this bridge process.
        "tokens": {},
        "paired": 0,
    }
    health = {"last_studio_poll": None, "last_backend_error": None}

    @asynccontextmanager
    async def lifespan(_):
        tasks = [asyncio.create_task(watcher.run())] if watch else []

        async def retry_outbox():
            delay = 2
            while True:
                await asyncio.sleep(delay)
                try:
                    await asyncio.to_thread(ledger.flush, backend)
                    delay = 2 if ledger.queue_size() == 0 else min(delay * 2, 30)
                except (OSError, ValueError):
                    delay = min(delay * 2, 30)

        tasks.append(asyncio.create_task(retry_outbox()))
        yield
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        backend.close()

    app = FastAPI(title="StudioSync Local Bridge", lifespan=lifespan)
    app.state.pairing, app.state.ledger, app.state.watcher = pairing, ledger, watcher
    app.state.workspace = workspace

    @app.middleware("http")
    async def local_auth(request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0].lower()
        if host not in {"127.0.0.1", "localhost"} or request.headers.get("origin"):
            return failure("LOCAL_ORIGIN", "Unexpected Host or browser Origin", 403)
        try:
            if int(request.headers.get("content-length", "0")) > 2_000_000:
                return failure("TOO_LARGE", "Request exceeds bridge limit", 413)
        except ValueError:
            return failure("VALIDATION", "Invalid request size", 422)
        if request.url.path == "/pair":
            return await call_next(request)
        supplied = request.headers.get("x-bridge-token", "")
        master = bool(supplied) and secrets.compare_digest(supplied, config.bridge_token)
        now = time.time()
        pairing["tokens"] = {
            t: expiry for t, expiry in pairing["tokens"].items() if expiry is None or expiry > now
        }
        studio = supplied in pairing["tokens"]
        if not master and not studio:
            return failure("UNAUTHENTICATED", "Local bridge credential required", 401)
        if studio and (
            request.url.path.startswith("/files/") or request.url.path in {"/pairing/code"}
        ):
            return failure(
                "FORBIDDEN", "Studio credential cannot access local source contents", 403
            )
        request.state.master = master
        if studio:
            health["last_studio_poll"] = now
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def invalid(_, exc):
        return failure("VALIDATION", "Invalid bridge request", 422)

    @app.post("/pair")
    def pair(payload: PairRequest):
        if pairing["attempts"] >= 5 or pairing["expires"] <= time.time():
            return failure(
                "PAIRING_EXPIRED", "Generate a fresh pairing code from the bridge CLI", 403
            )
        pairing["attempts"] += 1
        if not secrets.compare_digest(payload.code, pairing["code"]):
            return failure("PAIRING_FAILED", "Invalid one-time pairing code", 403)
        token = secrets.token_urlsafe(32)
        # The Studio credential stays valid until this bridge process exits; the plugin drops it
        # when Studio closes. Only the one-time pairing code above is short-lived.
        pairing["tokens"][token] = None
        pairing["expires"] = 0
        # Distinguish concurrent Studio windows of one developer for this bridge run.
        pairing["paired"] += 1
        return {
            "token": token,
            "expires_at": None,
            "studio_label": f"Studio {pairing['paired']}",
            "project_id": config.project_id,
            "developer_id": config.developer_id,
            "workspace_id": config.workspace_id,
        }

    @app.post("/disconnect")
    def disconnect(request: Request):
        # Studio may end its own session. The bridge master credential cannot be revoked here.
        pairing["tokens"].pop(request.headers.get("x-bridge-token", ""), None)
        return Result(data={"disconnected": True})

    @app.post("/pairing/code")
    def pairing_code():
        pairing.update(code=secrets.token_urlsafe(16), expires=time.time() + 300, attempts=0)
        return {"code": pairing["code"], "expires_at": pairing["expires"]}

    @app.get("/health")
    def bridge_health():
        result = backend.action("get_project_state", {"limit": 1})
        return {
            "status": "ok" if result.ok else "disconnected",
            "project_id": config.project_id,
            "watcher": watcher.health,
            "pending_writes": ledger.pending(),
            "outbox_size": ledger.queue_size(),
            **health,
        }

    @app.get("/project")
    def project():
        result = backend.action("get_project_state", {"limit": 200})
        if result.ok:
            result.data["workspace_id"] = config.workspace_id
        health["last_backend_error"] = result.error.message if result.error else None
        return result

    @app.get("/workspace")
    def workspace_state():
        return Result(
            data={
                "workspace_id": config.workspace_id,
                "mode": config.mode,
                "git": workspace.git(),
                "rojo": workspace.rojo(),
                "pending_writes": ledger.pending(),
                "outbox_size": ledger.queue_size(),
            }
        )

    studio_allowed = {
        "get_project_state",
        "list_agents",
        "register_agent",
        "heartbeat",
        "list_tasks",
        "create_task",
        "claim_task",
        "update_task",
        "get_task_dependencies",
        "add_dependency",
        "list_locks",
        "acquire_lock",
        "renew_lock",
        "release_lock",
        "check_lock",
        "override_lock",
        "send_message",
        "get_messages",
        "acknowledge_message",
        "get_interface",
        "get_activity",
        "check_conflicts",
        "get_resource_owner",
        "request_resource_transfer",
        "get_recent_changes",
        "get_affected_agents",
        "get_integration_status",
        "report_conflict",
        "resolve_conflict",
    }

    def add_action(operation, schema):
        def action(payload, request: Request) -> Result:
            if operation in {"prepare_write", "finish_write", "register_workspace"}:
                return Result(
                    ok=False,
                    error=Error(code="FORBIDDEN", message="Use dedicated workspace operations"),
                )
            if not request.state.master and operation not in studio_allowed:
                return Result(
                    ok=False,
                    error=Error(
                        code="FORBIDDEN", message="Operation requires the agent bridge credential"
                    ),
                )
            return backend.action(operation, payload.model_dump())

        action.__annotations__["payload"] = schema
        action.__name__ = operation
        app.post("/actions/" + operation, response_model=Result)(action)

    for operation, schema in OPERATIONS.items():
        add_action(operation, schema)

    for route, operation in {
        "agents": "list_agents",
        "tasks": "list_tasks",
        "locks": "list_locks",
        "activity": "get_activity",
        "conflicts": "check_conflicts",
    }.items():

        def register_read(path, name):
            def read(cursor: int = 0, limit: int = 50):
                payload = OPERATIONS[name].model_validate({"cursor": cursor, "limit": limit})
                return backend.action(name, payload.model_dump())

            app.get("/" + path)(read)

        register_read(route, operation)

    @app.post("/files/read")
    def read_file(payload: ResourceInput):
        try:
            path = workspace.path(payload.resource)
            content = workspace.bytes(path)
            return Result(
                data={
                    "content": content.decode("utf-8"),
                    "revision": revision(content),
                    "exists": path.exists(),
                    "resource": payload.resource,
                }
            )
        except (OSError, ValueError, UnicodeError) as exc:
            return failure("FILE_REJECTED", str(exc), 422)

    @app.post("/files/write")
    def write_file(payload: WriteRequest):
        permit_id = None
        applied = False
        try:
            if payload.workspace_id != config.workspace_id:
                return failure(
                    "WORKSPACE_MISMATCH", "Write targets a different registered workspace", 403
                )
            content = payload.content.encode("utf-8")
            if len(content) > 1_000_000 or revision(content) != payload.new_revision:
                return failure(
                    "REVISION_MISMATCH", "Content does not match the declared new revision", 422
                )
            with write_lock:
                path = workspace.path(payload.resource)
                old = workspace.bytes(path)
                if revision(old) != payload.expected_revision:
                    return failure("REVISION_MISMATCH", "Local file changed since it was read")
                authorized = backend.action(
                    "prepare_write", payload.model_dump(exclude={"content"})
                )
                if not authorized.ok:
                    return authorized
                permit_id = authorized.data["permit"]["id"]
                ledger.write(
                    permit_id, canonical(payload.resource), payload.new_revision, "PENDING"
                )
                # Re-check after backend round trip while cooperating local bridges remain serialized.
                if (
                    workspace.path(payload.resource) != path
                    or revision(workspace.bytes(path)) != payload.expected_revision
                ):
                    backend.action(
                        "finish_write",
                        {
                            "permit_id": permit_id,
                            "observed_revision": payload.expected_revision,
                            "outcome": "ABORT",
                        },
                    )
                    ledger.write(permit_id, payload.resource, payload.new_revision, "ABORTED")
                    return failure("REVISION_MISMATCH", "Local file changed during authorization")
                atomic_replace(path, content)
                applied = True
                result = backend.action(
                    "finish_write",
                    {
                        "permit_id": permit_id,
                        "observed_revision": payload.new_revision,
                        "outcome": "COMMIT",
                    },
                )
                if not result.ok:
                    return failure(
                        "UNCERTAIN_WRITE",
                        "File was replaced but central confirmation failed; reconcile before transfer",
                        details={"permit_id": permit_id, "revision": payload.new_revision},
                    )
                ledger.write(permit_id, payload.resource, payload.new_revision, "COMMITTED")
                return Result(
                    data={
                        "resource": payload.resource,
                        "revision": payload.new_revision,
                        "permit_id": permit_id,
                    }
                )
        except (OSError, ValueError, Timeout) as exc:
            if permit_id and not applied:
                try:
                    current = revision(workspace.bytes(workspace.path(payload.resource)))
                    if current == payload.expected_revision:
                        result = backend.action(
                            "finish_write",
                            {
                                "permit_id": permit_id,
                                "observed_revision": current,
                                "outcome": "ABORT",
                            },
                        )
                        if result.ok:
                            ledger.write(
                                permit_id, payload.resource, payload.new_revision, "ABORTED"
                            )
                except (OSError, ValueError):
                    pass
            return failure(
                "FILE_REJECTED", str(exc), details={"permit_id": permit_id, "applied": applied}
            )

    @app.post("/files/reconcile")
    def reconcile_file(payload: ResourceInput):
        try:
            with write_lock:
                path = workspace.path(payload.resource)
                actual = revision(workspace.bytes(path))
                results = []
                for pending in ledger.pending():
                    if pending["resource"] == canonical(payload.resource):
                        result = backend.action(
                            "finish_write",
                            {
                                "permit_id": pending["permit_id"],
                                "observed_revision": actual,
                                "outcome": "RECONCILE",
                            },
                        )
                        if not result.ok:
                            return result
                        ledger.write(
                            pending["permit_id"],
                            pending["resource"],
                            pending["revision"],
                            result.data["permit"]["status"],
                        )
                        results.append(result.data)
                return Result(data={"reconciled": results, "observed_revision": actual})
        except (OSError, ValueError, Timeout) as exc:
            return failure("FILE_REJECTED", str(exc))

    return app


def production_bridge():
    return create_bridge(pair_code=__import__("os").getenv("STUDIOSYNC_PAIR_CODE"))
