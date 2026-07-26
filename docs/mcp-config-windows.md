# Configurar MCP en Windows — Claude Code

Dónde viven los archivos de configuración de MCP en Windows y cómo agregar el servidor
`gx-rag` local. Para el runbook completo del stack nativo, ver
[windows-native-setup.md](./windows-native-setup.md).

> **Claude Code ≠ Claude Desktop.** Usan archivos distintos. Esta guía es para **Claude Code**
> (el CLI). La sección final cubre Claude Desktop.

## Los 3 scopes de Claude Code

Claude Code guarda servidores MCP en tres scopes, en dos ubicaciones:

| Scope | Archivo | Alcance |
|---|---|---|
| **project** | `<repo>\.mcp.json` | Compartido y versionado en el repo. **Es el que usa gx-rag.** |
| **local** (default del CLI) | `C:\Users\<TU_USUARIO>\.claude.json` | Privado tuyo, solo en ESE proyecto. |
| **user** | `C:\Users\<TU_USUARIO>\.claude.json` | Privado tuyo, en TODOS tus proyectos. |

En Windows el `~` de Unix se traduce a `C:\Users\<TU_USUARIO>`:

- **`C:\Users\<TU_USUARIO>\.claude.json`** → config principal (MCP scope `local` y `user`).
- **`<repo>\.mcp.json`** → MCP scope `project`.
- **`C:\Users\<TU_USUARIO>\.claude\settings.json`** → *settings* (permisos, `allowedTools`,
  hooks). **No** es para servidores MCP.

## No edites `.claude.json` a mano

Ese archivo lo administra Claude Code (historial, config, estado). Un error de sintaxis rompe
la sesión. Usá el CLI:

```powershell
# scope project (lo que usa gx-rag; requiere el .mcp.json en la raíz del repo)
claude mcp add gx-rag -- .venv\Scripts\python.exe -m src.server

# listar lo configurado
claude mcp list

# quitar
claude mcp remove gx-rag

# scope user (disponible en todos tus proyectos) — usar path ABSOLUTO
claude mcp add -s user gx-rag -- C:\ruta\a\gx-rag\.venv\Scripts\python.exe -m src.server
```

## Opción más simple para gx-rag: el `.mcp.json` del repo

El repo trae `.mcp.json.windows.example`. Copialo en la raíz y Claude Code lo detecta solo al
abrir esa carpeta:

```powershell
copy .mcp.json.windows.example .mcp.json
```

Contenido (server stdio local, sin secretos):

```json
{
  "mcpServers": {
    "gx-rag": {
      "command": ".venv\\Scripts\\python.exe",
      "args": ["-m", "src.server"]
    }
  }
}
```

> El path es **relativo** al repo, así que abrí/lanzá Claude Code desde la raíz del repo.

## Verificar

Dentro de Claude Code, comando `/mcp` → `gx-rag` debe figurar **connected**. Probá con la
tool `search_docs` (ej. `Microsoft.Data.SqlClient`) y confirmá que devuelve resultados SAC.

## Nota: Claude Desktop (distinto)

Si en vez del CLI usás la app **Claude Desktop**, NO lee `.mcp.json` ni `.claude.json`. Usa:

- **`%APPDATA%\Claude\claude_desktop_config.json`**

Y necesita **path absoluto** al `python.exe` del venv más `cwd` = raíz del repo:

```json
{
  "mcpServers": {
    "gx-rag": {
      "command": "C:\\ruta\\a\\gx-rag\\.venv\\Scripts\\python.exe",
      "args": ["-m", "src.server"],
      "cwd": "C:\\ruta\\a\\gx-rag"
    }
  }
}
```

Para consumir el **endpoint remoto del VPS** (HTTP + bearer) en vez del stdio local, ver
[conectar-mcp-cliente.md](./conectar-mcp-cliente.md).
