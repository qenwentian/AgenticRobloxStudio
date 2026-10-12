# Verified primary references

Implementation API names were checked against the current official references during this build:

- [MCP Python SDK v1](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x): stdio transport,
  FastMCP discovery, typed requests and structured output. Actual installed SDK: 1.30.0.
- [Plugin](https://create.roblox.com/docs/reference/engine/classes/Plugin): toolbar and
  CreateDockWidgetPluginGuiAsync.
- [DockWidgetPluginGui](https://create.roblox.com/docs/reference/engine/classes/DockWidgetPluginGui).
- [HttpService](https://create.roblox.com/docs/reference/engine/classes/HttpService): RequestAsync
  and JSONEncode/JSONDecode.
- [ScriptEditorService](https://create.roblox.com/docs/reference/engine/classes/ScriptEditorService):
  TextDocumentDidChange observation.
- [ScriptDocument](https://create.roblox.com/docs/reference/engine/classes/ScriptDocument): GetScript.
- [Luau releases](https://github.com/luau-lang/luau/releases): compiler used for syntax checks.
- [StyLua releases](https://github.com/JohnnyMorganz/StyLua/releases): Luau parser/formatter.
- [Official PostgreSQL Windows archives](https://www.enterprisedb.com/download-postgresql-binaries):
  PostgreSQL 18.6 used only in an isolated disposable test cluster.

PluginManager:CreatePlugin is deprecated and was used solely in the external Studio smoke
test harness to avoid installing/modifying the user's game. It is not used by shipped plugin code.
