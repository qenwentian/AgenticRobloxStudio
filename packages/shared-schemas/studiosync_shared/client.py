import httpx

from .config import Config
from .schemas import Error, Result


class Backend:
    def __init__(self, url: str, token: str, project_id: str, client: httpx.Client | None = None):
        if client is None:
            url = Config.secure_url(url)
        self.project_id = project_id
        self.client = client or httpx.Client(
            base_url=url, headers={"Authorization": "Bearer " + token}, timeout=10, trust_env=False
        )

    def action(self, operation: str, payload: dict | None = None) -> Result:
        try:
            response = self.client.post(
                f"/projects/{self.project_id}/actions/{operation}", json=payload or {}
            )
            data = response.json()
            if "ok" not in data:
                return Result(
                    ok=False,
                    error=Error(
                        code="BACKEND_ERROR",
                        message=f"Backend returned HTTP {response.status_code}",
                    ),
                )
            return Result.model_validate(data)
        except (httpx.HTTPError, ValueError):
            return Result(
                ok=False,
                error=Error(
                    code="BACKEND_UNAVAILABLE",
                    message="Central server unavailable; ownership cannot be verified",
                ),
            )

    def close(self):
        self.client.close()
