# Running gx-rag natively on Windows 11 (no Docker)

This runbook sets up the full gx-rag stack — backend API, MCP stdio server, ChromaDB
corpus, and the optional admin frontend — running **natively on Windows 11**, with **no
Docker** and **no cloud**. The system is pure-retrieval and fully local, so nothing here
depends on an external service.

Two things make this straightforward:

1. **The ChromaDB corpus is portable across operating systems** (it is SQLite + an hnswlib
   index). You **copy** the `data/chroma` folder from the machine that built it — no
   re-scraping and no re-embedding.
2. **Every dependency has a native Windows wheel** (torch runs on CPU, plus chromadb,
   sentence-transformers, pymupdf, etc.).

The only things that differ from macOS/Linux: `dev.sh` is bash-only (use `dev.ps1` instead),
and venv paths are `.venv\Scripts\...` rather than `.venv/bin/...`.

> **Important — the corpus is NOT in git.** `.gitignore` excludes `data/chroma/` and
> `data/scraped/`. A `git clone` brings the code and `data/syntax.db`, but **not** the
> vector corpus. You must transfer `data/chroma` separately (Step 3).

---

## 1. Prerequisites

Install (via `winget` or the official installers):

| Tool | Notes |
|------|-------|
| **Python 3.12** | Match the project baseline. Check **"Add python.exe to PATH"** during install. |
| **Node.js LTS** (≥18) | Only needed for the admin frontend. Skip if you only consume via MCP. |
| **Git for Windows** | Includes Git Bash, which provides `scp` (useful for the Step 3 alternative). |

```powershell
winget install Python.Python.3.12
winget install OpenJS.NodeJS.LTS
winget install Git.Git
```

Optional: if you later hit "path too long" errors (HuggingFace cache or `node_modules`),
enable long paths:

```powershell
# Run PowerShell as Administrator
New-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\FileSystem" `
  -Name LongPathsEnabled -Value 1 -PropertyType DWORD -Force
```

---

## 2. Get the code

```powershell
git clone <origin-url> gx-rag
cd gx-rag
```

---

## 3. Get the corpus (the non-obvious step)

Only **`data/chroma`** is required to serve retrieval. `data/syntax.db` comes with the
clone. `data/scraped/` (the raw `.md` source articles) is **not** needed to query — skip it.

**Recommended — copy from the machine that built the corpus (e.g. the Mac):**

1. On the source machine: `cd data && zip -r chroma.zip chroma`  (~300–490 MB)
2. Transfer `chroma.zip` (cloud drive / USB / LAN) into `gx-rag\data\` on Windows.
3. On Windows, unzip it in place:
   ```powershell
   Expand-Archive -Path .\data\chroma.zip -DestinationPath .\data\ -Force
   ```

**Alternative — pull from the VPS (same corpus), using Git Bash:**

```bash
scp -r gga@173.249.49.68:/home/admin/.openclaw/workspace/gx-bt-rag-mcp/data/chroma ./data/
```

**Verify** the corpus landed:

```powershell
Test-Path .\data\chroma\chroma.sqlite3   # should print True
```

---

## 4. Python environment (native)

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

- The install pulls **torch (CPU build)**, chromadb, and sentence-transformers — roughly 2 GB.
- With no GPU, embeddings run on **CPU**. That only matters when *indexing*; *querying* the
  existing corpus is light. An NVIDIA GPU + CUDA is an optional speedup, not required.
- Always use the venv — a stray global package named `scripts` can shadow this repo's
  `scripts/` package; the venv isolates it.

> If `Activate.ps1` is blocked by execution policy, either run
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` for the current session, or
> just call the venv executables directly (`.venv\Scripts\python.exe`, `.venv\Scripts\uvicorn.exe`).

---

## 5. Run the stack

```powershell
.\dev.ps1
```

`dev.ps1` starts the backend (uvicorn on **:8050**) and, if `frontend/` is present, the Vite
dev server (on **:3050**) with its `/api` proxy pointed at the backend. Override ports with
`$env:BACKEND_PORT` / `$env:FRONTEND_PORT`.

To run only the backend, or the MCP server, use the venv directly:

```powershell
.venv\Scripts\uvicorn src.api:app --reload --port 8050   # HTTP API
.venv\Scripts\python -m src.server                        # MCP server (stdio)
```

**First run downloads the embedding model.** On the first search or index, `sentence-transformers`
fetches `intfloat/multilingual-e5-large` (~2.2 GB) from Hugging Face into
`C:\Users\<you>\.cache\huggingface`. This needs internet **once**; afterwards it runs offline.

---

## 6. Connect an MCP client (local stdio)

The stdio server (`src/server.py`) is what a local Claude Code / Claude Desktop consumes. The
repo's `.mcp.json` uses a POSIX path (`.venv/bin/python`) that does **not** work on Windows —
use a Windows path instead.

**Claude Code** — from the repo root:

```powershell
claude mcp add gx-rag -- .venv\Scripts\python.exe -m src.server
```

or an `.mcp.json` entry:

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

**Claude Desktop** — add the equivalent to `claude_desktop_config.json`, using the **absolute**
path to `.venv\Scripts\python.exe` and setting the working directory to the repo root:

```json
{
  "mcpServers": {
    "gx-rag": {
      "command": "C:\\path\\to\\gx-rag\\.venv\\Scripts\\python.exe",
      "args": ["-m", "src.server"],
      "cwd": "C:\\path\\to\\gx-rag"
    }
  }
}
```

> The remote-HTTP guide (`docs/conectar-mcp-cliente.md`) covers connecting to the VPS
> endpoint, not this local stdio path.

---

## 7. Verify (end-to-end)

1. **Backend up**: open `http://127.0.0.1:8050/docs` (Swagger loads); `GET /health` → `{"status":"ok"}`.
2. **Retrieval works on the copied corpus**: query `Microsoft.Data.SqlClient` (via `/docs` or
   `GET /search?q=...`) → the top hit is **SAC #61037** with a clean title (no "Recuerda que"
   boilerplate). This proves the ported corpus + retriever both work on Windows.
3. **MCP stdio**: `.venv\Scripts\python -m src.server` starts without error; in Claude Code
   `/mcp` shows `gx-rag` connected; `search_docs("Postgresql NOWAIT")` returns SAC hits.
4. **Frontend (optional)**: `http://localhost:3050` loads the admin panel.
5. **Corpus count** (sanity):
   ```powershell
   .venv\Scripts\python -c "import chromadb; c=chromadb.PersistentClient(path='data/chroma'); print({col.name: c.get_collection(col.name).count() for col in c.list_collections()})"
   ```
   Expect `gx_docs` ≈ 50056.
