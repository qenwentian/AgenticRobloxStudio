import asyncio

from fastapi.testclient import TestClient
from studiosync_mcp.server import create_mcp
from studiosync_shared.client import Backend
from studiosync_shared.schemas import OPERATIONS


def test_sdk_discovery_schemas_and_real_backend_calls(team):
    user = team["users"][0]
    client = TestClient(team["app"])
    client.headers.update(user["headers"])
    adapter = create_mcp(Backend("", "", team["project"], client))

    async def exercise():
        tools = await adapter.list_tools()
        names = {t.name for t in tools}
        assert {
            "coordination." + name
            for name in OPERATIONS
            if name not in {"prepare_write", "finish_write"}
        } <= names
        for tool in tools:
            assert tool.inputSchema["type"] == "object"
            assert tool.outputSchema["type"] == "object"
        result = await adapter.call_tool("coordination.list_agents", {"request": {}})
        assert result[1]["ok"]
        assert len(result[1]["data"]["agents"]) == 5
        task = await adapter.call_tool(
            "coordination.create_task",
            {
                "request": {
                    "session_id": user["session"],
                    "title": "From real MCP",
                    "resources": ["file:src/a.luau"],
                }
            },
        )
        assert task[1]["data"]["task"]["title"] == "From real MCP"

    asyncio.run(exercise())
