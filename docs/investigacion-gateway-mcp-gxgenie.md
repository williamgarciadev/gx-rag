# Investigación: protocolo MCP del gateway GxGenie (C#)

> **Procedencia**: este texto estaba pegado al final de `RAG_SYSTEM_MAP.md`, junto con la
> notificación del agente que lo produjo (se conserva abajo tal cual). No describe gx-rag:
> es una investigación sobre el gateway MCP en C# de otro proyecto. Se movió acá el
> 2026-10-17 para que el mapa del sistema hable solo del sistema.

---

[TASK NOTIFICATION]
ID: annoyed-olive-swallow
Status: complete
Agent: All research complete. One imp...

Result:

All research complete. One important correction up front: **the catalog has 26 tools, not 28** — verified with a direct count of `Name = "gx_` declarations in `ToolSchemas.cs` (rg output: 26 matches, all listed in section 8). Full report below.

---

# Gateway MCP Protocol Implementation — Migration Research Report

## 1. JSON-RPC methods handled & protocol version

All dispatch lives in `McpServer.HandleAsync` (`GxGenie.Gateway\McpServer.cs:73-130`), a hand-rolled line-delimited JSON-RPC 2.0 loop (`RunAsync`, lines 37-71: `ReadLineAsync` → `JsonNode.Parse` → dispatch → `WriteLineAsync`).

| Method | Handling | Reference |
| --- | --- | --- |
| `initialize` | Returns static `BuildInitializeResult()` | McpServer.cs:90-92, 132-144 |
| `initialized` **and** `notifications/initialized` | No-op, no response | McpServer.cs:94-97 |
| `tools/list` | Returns full catalog, ignores `params.cursor` (no pagination) | McpServer.cs:99-101, 146-159 |
| `tools/call` | Dispatches to Worker via `WorkerProxy` | McpServer.cs:103-106, 161-215 |
| `ping` | Returns empty `{}` result | McpServer.cs:108-110 |
| `shutdown` | Returns empty `{}` result (non-MCP, LSP-style extra) | McpServer.cs:112-114 |
| Unknown method **with id** | JSON-RPC error `-32601 Method not found` | McpServer.cs:116-118 |
| Unknown **notification** | Logged to stderr, ignored | McpServer.cs:119-120 |

Protocol version: **`"2024-11-05"`**, hardcoded constant (McpServer.cs:14). **There is no actual negotiation** — `BuildInitializeResult()` is parameterless and always echoes `"2024-11-05"` regardless of what the client sent; `params.protocolVersion` is never read. The doc comment (McpServer.cs:10) explicitly targets spec 2024-11-05.

Other protocol-level behaviors:

- **Notification detection**: `bool isNotification = idNode is null` (McpServer.cs:78) — notifications never get a response; responses echo the request `id` via `DeepClone` (lines 234, 242), so both numeric and string ids work.
- **JSON-RPC errors used**: `-32700` parse error (line 59), `-32600` invalid request / missing method (lines 64, 82), `-32601` method not found (line 118), `-32603` internal error via catch-all (line 128).
- **Not implemented**: `notifications/cancelled` (falls into "unknown notification" ignore path), progress, `logging/*`, resources, prompts, sampling, roots. No `instructions` field in initialize result.
- Edge-case quirk: `req["method"]?.GetValue<string>()` (line 76) runs *outside* the try block — a non-string `method` value crashes the process (FATAL in Program.cs:58-61) instead of returning `-32600`.
- **No input validation** of `tools/call` arguments against `inputSchema` — validation is delegated entirely to the Worker.

## 2. Exact `initialize` response shape

From `BuildInitializeResult()` (McpServer.cs:132-144) with constants at lines 14-16:

```json
{
  "protocolVersion": "2024-11-05",
  "capabilities": {
    "tools": { "listChanged": false }
  },
  "serverInfo": {
    "name": "gxmcp",
    "version": "1.4.0"
  }
}
```

- Server name: **`gxmcp`** (McpServer.cs:15) — note: *not* "GxGenie".
- Server version: **`1.4.0`** (McpServer.cs:16), hardcoded, not from assembly metadata.
- Only capability advertised is `tools.listChanged = false`. No resources/prompts/logging/sampling/roots capabilities.

## 3. Tool schema representation & tools/call dispatch

### Schema representation (ToolSchemas.cs)

**Not raw JSON strings and not attributed C# types.** Schemas are `System.Text.Json.Nodes.JsonObject` trees **built programmatically in code** at static-init time, inside a static `List<ToolDef>` (ToolSchemas.cs:150-678). The catalog entry type (ToolSchemas.cs:13-19):

```csharp
public sealed class ToolDef
{
    public string Name { get; init; } = "";
    public string Description { get; init; } = "";
    public JsonNode InputSchema { get; init; } = new JsonObject();
    public string WorkerTool { get; init; } = "";
}
```

Key points:

- Builder helpers: `Obj(...)` (line 21), `Prop(type, description, extra)` (line 28 — merges `enum`/`default`/`pattern`/`minimum`/`maximum` siblings into the property), `EnumOf(...)` (line 42), `ModuleProp()` (line 55 — rebuilt per call site because a `JsonNode` can't have two parents).
- Descriptions/params are **100% static and hand-written** (Spanish prose), with two dynamic touches: the shared `ObjectTypes` string array (lines 143-148, 20 GX types) feeds several `enum`s, and `gx_create_transaction` uses a dedicated method `CreateTransactionInputSchema()` (lines 66-140) with recursive `$defs`/`$ref` (`#/$defs/attribute`, `#/$defs/level`).
- `Name` → `WorkerTool` is currently **1:1 identical for all 26 tools** (e.g. lines 154-155), but the indirection exists and the SDK migration must preserve it.
- `FindByName` is ordinal exact match (lines 680-681).
- No `outputSchema`, no `annotations`, no `_meta` on any tool (doc comment lines 8-9 says outputSchema is deliberately omitted).
- `tools/list` emits `{ name, description, inputSchema: <DeepClone> }` per tool (McpServer.cs:146-159).

### Exact JSON shape of one full tool (as serialized by `tools/list`)

`gx_list_objects` (ToolSchemas.cs:163-186):

```json
{
  "name": "gx_list_objects",
  "description": "Lista objetos de la KB filtrando por tipo y/o substring del nombre. Use type='All' para listar todos los tipos top-level.",
  "inputSchema": {
    "type": "object",
    "properties": {
      "type": {
        "type": "string",
        "description": "Tipo de objeto a listar. 'All' devuelve todos los tipos top-level.",
        "enum": ["All","Transaction","Procedure","WebPanel","DataProvider","DataSelector","DataView","BusinessComponent","SDT","Domain","Table","Theme","Query","Report","Menu","WorkPanel","Image","ExternalObject","Group","BPDiagram"],
        "default": "All"
      },
      "filter": {
        "type": "string",
        "description": "Substring case-insensitive a buscar en el nombre del objeto (opcional)."
      },
      "limit": {
        "type": "integer",
        "description": "Máximo de filas a devolver (default 1000).",
        "minimum": 1,
        "maximum": 100000,
        "default": 1000
      }
    },
    "additionalProperties": false
  }
}
```

(The zero-arg tools `gx_kb_info` / `gx_list_kbs` use `"properties": {}` + `"additionalProperties": false`, no `"required"` — ToolSchemas.cs:157-161, 672-676.)

### tools/call dispatch & error propagation (McpServer.cs:161-228)

Flow:

1. `params` must be a JSON object with non-empty `name` → otherwise **tool error** (not JSON-RPC error), lines 163-168.
2. Unknown tool name → tool error `"Unknown tool: {name}"` (lines 170-172).
3. `arguments` (if present) round-tripped `JsonNode → ToJsonString → JsonDocument → JsonElement.Clone()` (lines 174-180). Note: non-object `arguments` are accepted here but silently dropped by WorkerProxy (see below).
4. `await _worker.CallAsync(tool.WorkerTool, arguments, ct)` (line 185). Any exception → tool error `"Worker call failed: {ExType}: {msg}"` (lines 187-190).

**Worker response contract** (consumed at McpServer.cs:192-201): `{ "success": bool, "error"?: string, "data"?: any }`.

- `success != true` → tool error `"Worker error: {error}"` (or `"(no error message)"`).
- Success → `data` re-serialized **pretty-printed** (`WriteIndented = true`, line 200) into a single text content item; missing `data` → literal string `"(no data)"`.

Success envelope (lines 203-214) / error envelope (`ToolError`, lines 217-228):

```json
{ "content": [ { "type": "text", "text": "<pretty JSON of data>" } ], "isError": false }
{ "content": [ { "type": "text", "text": "<message>" } ], "isError": true }
```

**Critical design fact for migration: worker/tool failures NEVER become JSON-RPC errors.** They always come back as a normal `result` with `isError: true` (MCP tool error). JSON-RPC `error` responses are reserved for protocol faults (parse/dispatch/internal, section 1). The SDK migration must preserve this split — e.g. by catching exceptions inside the tool handler and returning `CallToolResult` with `IsError = true` rather than letting them surface as JSON-RPC errors.

## 4. Worker lifecycle (WorkerProxy.cs)

- **Long-lived child process, lazily spawned** on first `CallAsync` via `EnsureStarted()` (WorkerProxy.cs:49, 89-127). Rationale in remarks (lines 12-16): restarting per call would re-attach LocalDB every time.
- `ProcessStartInfo` (lines 97-108): `UseShellExecute=false`, all three stdio streams redirected, `CreateNoWindow=true`, `StandardOutputEncoding`/`StandardErrorEncoding = UTF8`, `WorkingDirectory` = Gateway's own cwd (inherited from Claude Code, for per-KB `.gxw` auto-detect).
- **Env vars passed** (lines 109-115):
  - `GXGENIE_CWD` = user cwd, **always**.
  - `GXGENIE_CONFIG` = config path, **only if explicit** (`--config` arg or `GXGENIE_CONFIG` env — `GatewayConfig.IsExplicitConfig`, GatewayConfig.cs:30, 44-47). Fallback configs (next to exe / repo root) are *not* propagated; the Worker does its own resolution.
- **Concurrency**: `SemaphoreSlim(1,1)` serializes all calls (lines 23, 46, 85) — concurrent `tools/call`s queue, they don't interleave. The SDK migration must keep single-flight semantics or switch the Worker IPC.
- **IPC request shape** (one JSON line to worker stdin, lines 52-62):

  ```json
  { "tool": "<WorkerTool>", "id": "<Guid:N 32 hex>", "params": { ... } }
  ```

  `params` included **only if** arguments are a JSON object (line 57). The `id` is generated per call and is *not* correlated on response — responses are matched positionally (one request → next line).
- **IPC response**: exactly one line from worker stdout, parsed to `JsonElement` (lines 67-81). Shape: `{ success, error?, data? }` (see §3).
- **Timeout**: linked CTS `CancelAfter(WorkerTimeoutSeconds)` (lines 64-65); default **120 s** (GatewayConfig.cs:20, overridable via `config.json` `Worker.TimeoutSeconds`, GatewayConfig.cs:66). Timeout → `TimeoutException` with drained stderr embedded (line 75).
- **Crash handling**: `ReadLine` returning `null` → `InvalidOperationException("Worker exited unexpectedly. Stderr:...")` (lines 78-79). **Restart is implicit/lazy**: `EnsureStarted` checks `_process is { HasExited: false }` (line 91), so the *next* call after a crash spawns a fresh process. The failed call itself is not retried — it surfaces as a tool error. `EnableRaisingEvents = true` is set (line 117) but no `Exited` handler is attached; the dead `Process` object is never disposed until gateway shutdown (minor leak).
- **stderr handling**: async `BeginErrorReadLine` + `ErrorDataReceived` appending to a lock-guarded `StringBuilder` (lines 24, 118-122, 125); only ever surfaced inside timeout/crash exception messages via `DrainStderr()` (lines 129-136). Never forwarded to MCP `logging` notifications or anywhere else.
- **Dispose** (lines 138-159): close worker stdin → `WaitForExit(2000)` → `Kill(entireProcessTree: true)` fallback → dispose.

Worker exe resolution (`GatewayConfig.Load` + `AutoDetectWorkerExe`, GatewayConfig.cs:32-110): `config.json` `Worker.ExecutablePath` → env `GXGENIE_WORKER_EXE` → sibling of gateway dir → repo layout `..\..\..\..\GxGenie.Worker\bin\{Release,Debug}\net8.0\`. **No `ProjectReference`** — the Worker is purely an external process.

## 5. Logging & other protocol-relevant behavior

- **Logging = stderr only**, prefix `[gxmcp.gateway]` (`Program.cs:37`); no file logging anywhere. Stdout is reserved for JSON-RPC lines; `Console.OutputEncoding`/`InputEncoding` forced to UTF-8 (`Program.cs:6-7`).
- Log points: startup worker/config/cwd (`Program.cs:39-41`), `"MCP server ready (protocol 2024-11-05, 26 tools)"` (McpServer.cs:39), `"MCP server exiting (stdin closed)"` (McpServer.cs:70), unknown notifications (McpServer.cs:120), handler errors (McpServer.cs:126), `FATAL` + exit 1 on unhandled (Program.cs:58-61).
- **Framing**: newline-delimited JSON (`ReadLineAsync`/`WriteLineAsync` + `FlushAsync`, McpServer.cs:45, 250-255); compact serialization, nulls omitted (`JsonOpts`, McpServer.cs:23-27).
- **Cancellation**: Ctrl+C wired to a CTS (`Program.cs:46-51`), token flows into `ReadLineAsync` and worker calls. **No MCP `notifications/cancelled` support** — it's ignored as an unknown notification. No in-flight request is cancelled by the client.
- **No progress notifications, no timeouts at the MCP layer** (only the Worker-level 120 s timeout).
- **Shutdown**: stdin EOF ends the loop → exit 0 (McpServer.cs:49, 70; Program.cs:56). `shutdown` method returns `{}` but does not stop the loop.
- Exit codes: 0 normal, 1 fatal, 2 config load failure (Program.cs:34, 56, 61).

## 6. Target framework & NuGet dependencies (GxGenie.Gateway.csproj)

- `net8.0`, `OutputType=Exe`, `Nullable=enable`, `ImplicitUsings=enable`, `LangVersion=latest`, `InvariantGlobalization=true` (csproj lines 3-14).
- **Zero `<PackageReference>` items. Zero `<ProjectReference>` items.** The entire Gateway runs on BCL only (`System.Text.Json`). Everything — JSON-RPC framing, MCP protocol, schema catalog, process management — is hand-rolled. This is a green-field insertion point for `ModelContextProtocol` + `Microsoft.Extensions.Hosting` packages.

## 7. `C:\Proyectos\GxGenie` existence

**`Test-Path 'C:\Proyectos\GxGenie'` → `False`.** The directory does not exist on this machine, confirming the AGENTS.md gotcha: both test scripts are unrunnable here without edits. Hardcoded references:

- `test-mcp.ps1:5` (exe path), `:8` (`GXGENIE_CONFIG = C:\Proyectos\GxGenie\config.json`)
- `test-mcp-write.ps1:5` (exe), `:8` (`config.test.json`), `:76` (XPZ output into `C:\Proyectos\GxGenie\GxGenie.Worker\probes\`)

What the scripts do (relevant to migration regression testing):

- **test-mcp.ps1** (read-only smoke): sends `initialize` (protocolVersion `2024-11-05`, empty capabilities, clientInfo `test/0`), `tools/list` (prints count + names), `tools/call gx_kb_info`, `tools/call gx_list_objects {type=Transaction, filter=BOrden, limit=3}`; parses `result.content[0].text` as JSON and prints fields (`kbName`, `kbVersion`, `totalEntities`, `models`, `objectCounts`, `count`, `items`); closes stdin, waits 5 s, prints exit code + stderr. **No hard assertions** beyond "exe exists".
- **test-mcp-write.ps1** (write roundtrip, needs `config.test.json`): initialize → tools/list → `gx_kb_info` → `gx_create_procedure` (random `MCPGwTest_<n>`; **throws if `result.isError`**, checks `payload.backup_path`) → `gx_list_objects` (**throws if `count < 1`**, reads `items[0].id`) → `gx_read_object` (**throws unless `parts.source` contains "via gateway"**) → `gx_export_xpz` (**throws unless `payload.success`**, reads `bytes`/`output_path`). This script *does* assert, and its assertions depend on the exact MCP result shape (`result.isError`, `result.content[0].text` being parseable JSON) — i.e. the `isError`/text-content contract from §3 is load-bearing.

## 8. The tool list — correction: **26 tools, not 28**

Verified by counting `Name = "gx_` declarations in `ToolSchemas.cs` (rg: 26 matches). There is no other file contributing tools (`ToolSchemas.All` is the single source, and `McpServer` only reads it). If you expected 28, that number doesn't match the code — the complete list, in catalog order:

| # | Tool name | Line | Category (per source comments) |
| --- | --- | --- | --- |
| 1 | `gx_kb_info` | 154 | Read |
| 2 | `gx_list_objects` | 165 | Read |
| 3 | `gx_read_object` | 189 | Read |
| 4 | `gx_search` | 208 | Read |
| 5 | `gx_list_attributes` | 238 | Read |
| 6 | `gx_export_xpz` | 256 | Phase 3 write |
| 7 | `gx_import_xpz` | 278 | Phase 3 write |
| 8 | `gx_build_object` | 300 | Phase 3 write (AllowBuild) |
| 9 | `gx_delete_object` | 316 | Phase 3 write |
| 10 | `gx_create_procedure` | 337 | Phase 3 write |
| 11 | `gx_create_transaction` | 358 | Phase 3 write |
| 12 | `gx_update_object_code` | 366 | Phase 3 write |
| 13 | `gx_get_structure` | 394 | Phase B1 structured read |
| 14 | `gx_get_layout` | 414 | Phase B1 structured read |
| 15 | `gx_get_variables` | 434 | Phase B1 structured read |
| 16 | `gx_get_unused_variables` | 454 | Phase B1 structured read |
| 17 | `gx_add_attribute` | 476 | Phase B2 granular writes |
| 18 | `gx_remove_attribute` | 514 | Phase B2 granular writes |
| 19 | `gx_remove_variable` | 531 | Phase B2 granular writes |
| 20 | `gx_set_attribute_property` | 550 | Phase B2 granular writes |
| 21 | `gx_set_control_property` | 569 | Phase B3 layout writes |
| 22 | `gx_remove_control` | 590 | Phase B3 layout writes |
| 23 | `gx_add_control` | 606 | Phase B3 layout writes |
| 24 | `gx_list_object_parts` | 632 | Phase B3 discovery |
| 25 | `gx_switch_kb` | 654 | Phase 4 multi-KB |
| 26 | `gx_list_kbs` | 669 | Phase 4 multi-KB |

---

### Migration-critical summary

1. **Protocol surface is tiny**: initialize (no real version negotiation), tools/list, tools/call, ping, plus notification tolerance. The official SDK covers all of it; the only behaviors to deliberately preserve are the `serverInfo` identity (`gxmcp` / `1.4.0`), the `isError:true`-not-JSON-RPC-error contract for worker failures, pretty-printed `data` inside a single text content item, and the absence of `outputSchema`.
2. **The Worker IPC is orthogonal to MCP** — `WorkerProxy` can survive the migration almost intact; the SDK's tool handlers just replace `HandleToolCallAsync`. Keep the `SemaphoreSlim` serialization, lazy restart, 120 s timeout, and stderr capture.
3. **Schemas are code-built `JsonNode` trees with rich constraints** (`enum`, `pattern`, `minimum/maximum`, `default`, recursive `$defs/$ref` in `gx_create_transaction`). Whatever SDK mapping you choose must not lose those constraints or the Spanish descriptions.
4. **Zero existing NuGet deps** means no version conflicts, but also note `LangVersion=latest` + net8.0 — the `ModelContextProtocol` SDK targets net8.0, so that's compatible.
5. Test scripts are **not runnable on this machine** (`C:\Proyectos\GxGenie` absent) and hardcode paths at test-mcp.ps1:5,8 and test-mcp-write.ps1:5,8,76 — plan to parameterize them as part of the migration if you want regression coverage.
