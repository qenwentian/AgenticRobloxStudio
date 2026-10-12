import json
import os
from pathlib import Path
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_url: str = "http://127.0.0.1:8765"
    project_id: str
    workspace_id: str
    workspace_root: str
    developer_id: str
    mode: str = "GIT_ROJO"
    bridge_port: int = Field(default=8766, ge=1024, le=65535)
    bridge_token: str = Field(min_length=32)

    @field_validator("server_url")
    @classmethod
    def secure_url(cls, value):
        parsed = urlparse(value)
        if not parsed.hostname or parsed.path not in {"", "/"}:
            raise ValueError("Server URL requires a hostname and no path prefix")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Server URL must not embed credentials, query or fragment")
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        ):
            raise ValueError("Remote coordination connections require HTTPS")
        return value.rstrip("/")

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, value):
        if value not in {"GIT_ROJO", "TEAM_CREATE"}:
            raise ValueError("Unknown workspace mode")
        return value


def load_config(path: str | None = None) -> Config:
    path = path or os.getenv("STUDIOSYNC_CONFIG", ".studiosync.json")
    return Config.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def backend_token() -> str:
    value = os.getenv("STUDIOSYNC_TOKEN", "")
    if not value:
        from .credentials import load_login

        return load_login(load_config())
    return value
