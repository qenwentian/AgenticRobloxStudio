"""stdio MCP adapter; coordination state lives exclusively in the backend."""

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field
from studiosync_shared.client import Backend
from studiosync_shared.config import backend_token, load_config
from studiosync_shared.schemas import OPERATIONS, Error, PrepareWrite, Result


class ControlledWrite(PrepareWrite):
    content: str = Field(max_length=1_000_000)


def create_mcp(backend: Backend, config=None) -> FastMCP:
    server = FastMCP("StudioSync AI")
    for operation, schema in OPERATIONS.items():
        if operation in {"prepare_write", "finish_write"}:
            continue  # Only the bridge should implement the filesystem commit protocol.

        def register(name, model):
            def call(request) -> Result:
                return backend.action(name, request.model_dump())

            call.__annotations__["request"] = model
            call.__name__ = name
            server.add_tool(
                call,
                name="coordination." + name,
                description=f"{name.replace('_', ' ').capitalize()}. Project membership required; mutations require developer access. Session-bound operations act only as your developer. Consult the returned error before continuing.",
            )

        register(operation, schema)

    @server.tool(name="coordination.get_workspace_context")
    def context() -> Result:
        """Return configured project/workspace identity without credentials. Query state before work."""
        if not config:
            return Result(data={"project_id": backend.project_id})
        return Result(
            data={
                "project_id": config.project_id,
                "workspace_id": config.workspace_id,
                "developer_id": config.developer_id,
                "mode": config.mode,
            }
        )

    @server.tool(name="workspace.controlled_write")
    def write(request: ControlledWrite) -> Result:
        """Atomically replace a local UTF-8 file through the authenticated bridge with lease/fence/hash checks."""
        if not config:
            return Result(
                ok=False,
                error=Error(code="BRIDGE_REQUIRED", message="Configure a local workspace bridge"),
            )
        try:
            with httpx.Client(trust_env=False, timeout=15) as client:
                response = client.post(
                    f"http://127.0.0.1:{config.bridge_port}/files/write",
                    headers={"X-Bridge-Token": config.bridge_token},
                    json=request.model_dump(),
                )
                return Result.model_validate(response.json())
        except (httpx.HTTPError, ValueError):
            return Result(
                ok=False,
                error=Error(
                    code="BRIDGE_UNAVAILABLE",
                    message="Bridge unavailable; file write was not confirmed",
                ),
            )

    class FileRead(BaseModel):
        resource: str

    @server.tool(name="workspace.read_file")
    def read(request: FileRead) -> Result:
        """Read a workspace file and its SHA-256 revision locally; contents are not sent to the server."""
        if not config:
            return Result(
                ok=False, error=Error(code="BRIDGE_REQUIRED", message="Configure the local bridge")
            )
        try:
            with httpx.Client(trust_env=False, timeout=10) as client:
                response = client.post(
                    f"http://127.0.0.1:{config.bridge_port}/files/read",
                    headers={"X-Bridge-Token": config.bridge_token},
                    json=request.model_dump(),
                )
                return Result.model_validate(response.json())
        except (httpx.HTTPError, ValueError):
            return Result(
                ok=False, error=Error(code="BRIDGE_UNAVAILABLE", message="Cannot read workspace")
            )

    return server


def main():
    import logging
    import os
    import sys

    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = load_config()
    backend = Backend(config.server_url, backend_token(), config.project_id)
    try:
        # Remote hosting can mount streamable_http_app with separate authenticated deployment config.
        # The SDK's UTF-8 wrappers own their buffers. Give them duplicate handles so
        # shutdown cannot close the interpreter's original streams (including frozen apps).
        original_stdin, original_stdout = sys.stdin, sys.stdout
        with (
            os.fdopen(os.dup(original_stdin.fileno()), "r", encoding="utf-8") as stdin,
            os.fdopen(os.dup(original_stdout.fileno()), "w", encoding="utf-8") as stdout,
        ):
            sys.stdin, sys.stdout = stdin, stdout
            try:
                create_mcp(backend, config).run(transport="stdio")
            finally:
                sys.stdin, sys.stdout = original_stdin, original_stdout
    finally:
        backend.close()


if __name__ == "__main__":
    main()
