import json
from pathlib import Path

from studiosync_server.app import production_app
from studiosync_shared.schemas import OPERATIONS, Result

root = Path(__file__).resolve().parents[1]
contracts = {
    "output": Result.model_json_schema(),
    "inputs": {operation: model.model_json_schema() for operation, model in OPERATIONS.items()},
}
(root / "packages/shared-schemas/contracts.json").write_text(
    json.dumps(contracts, indent=2), encoding="utf-8"
)
app = production_app()
(root / "packages/shared-schemas/openapi.json").write_text(
    json.dumps(app.openapi(), indent=2), encoding="utf-8"
)
app.state.engine.dispose()
print("Exported actual action and OpenAPI contracts")
