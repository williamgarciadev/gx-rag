# Paso a paso — dejar gx-rag igualito en Windows 11 (nativo, sin Docker)

Guía lineal para copiar y pegar. Al terminar tenés en tu Windows **lo mismo que en la Mac**:
mismo código, mismo corpus de 50k chunks, backend + frontend + MCP local funcionando.

Referencia técnica: [windows-native-setup.md](./windows-native-setup.md) ·
Config MCP: [mcp-config-windows.md](./mcp-config-windows.md)

> ⚠️ **Dos cosas que NO son obvias y arruinan todo si las salteás:**
> 1. El código está en la branch **`feat/hybrid-retrieval-and-api-hardening`**, NO en `main`.
>    Si clonás sin la branch, te falta la feature SAC y el fix del retriever.
> 2. El **corpus NO viene en el clone** (está gitignoreado). Hay que copiarlo aparte (paso 4).

---

## Paso 1 — Instalar los programas base

Abrí **PowerShell** y corré:

```powershell
winget install Python.Python.3.12
winget install OpenJS.NodeJS.LTS
winget install Git.Git
```

**Cerrá y volvé a abrir PowerShell** (para que tome el PATH). Verificá:

```powershell
python --version    # -> Python 3.12.x
node --version      # -> v20.x o similar
git --version
```

---

## Paso 2 — Clonar el repo (con la branch correcta)

Elegí una carpeta (ej. `C:\Proyectos`) y cloná **especificando la branch**:

```powershell
cd C:\Proyectos
git clone -b feat/hybrid-retrieval-and-api-hardening https://github.com/williamgarciadev/gx-rag.git
cd gx-rag
```

Confirmá que estás en la branch correcta:

```powershell
git branch --show-current    # -> feat/hybrid-retrieval-and-api-hardening
```

---

## Paso 3 — Preparar el corpus en la Mac (hacelo ANTES de mover nada)

En la **Mac** ya generé el zip del corpus. Está en:

```
/Users/geovannygarcia/Proyectos/gx-rag/data/chroma.zip   (289 MB)
```

Si necesitás regenerarlo, en la Mac:

```bash
cd ~/Proyectos/gx-rag/data && zip -r chroma.zip chroma
```

---

## Paso 4 — Traer el corpus a la Windows (el paso clave)

Copiá `chroma.zip` de la Mac a la Windows por **el medio que tengas** (Google Drive, OneDrive,
USB, red local). Dejalo dentro de `C:\Proyectos\gx-rag\data\`.

Después, en PowerShell (parado en la raíz del repo):

```powershell
Expand-Archive -Path .\data\chroma.zip -DestinationPath .\data\ -Force
```

Verificá que quedó bien:

```powershell
Test-Path .\data\chroma\chroma.sqlite3    # -> True
```

> Alternativa (si tenés acceso SSH al VPS): en **Git Bash**,
> `scp -r gga@173.249.49.68:/home/admin/.openclaw/workspace/gx-bt-rag-mcp/data/chroma ./data/`

---

## Paso 5 — Crear el entorno Python e instalar dependencias

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Esto baja ~2 GB (torch en CPU, chromadb, sentence-transformers). Tardá lo que tarde.

> Si `Activate.ps1` da error de permisos, corré una vez en esa ventana:
> `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` y reintentá.

---

## Paso 6 — Configurar el MCP local

```powershell
copy .mcp.json.windows.example .mcp.json
```

Eso deja el server `gx-rag` (stdio, path del venv de Windows, sin tokens) listo para Claude Code.

---

## Paso 7 — Levantar todo

```powershell
.\dev.ps1
```

- Backend → http://127.0.0.1:8050  (docs en `/docs`)
- Frontend → http://localhost:3050  (panel de administración)

**El primer arranque baja el modelo de embeddings** `intfloat/multilingual-e5-large`
(~2.2 GB) desde HuggingFace a `C:\Users\<TU_USUARIO>\.cache\huggingface`. Necesita internet
**una sola vez**; después funciona offline. Cortás con **Ctrl+C**.

---

## Paso 8 — Conectar Claude Code al MCP

Desde la raíz del repo:

```powershell
claude mcp add gx-rag -- .venv\Scripts\python.exe -m src.server
claude mcp list
```

(O simplemente abrí Claude Code en la carpeta del repo: detecta el `.mcp.json` solo.)

---

## Paso 9 — Verificar que quedó igual que en la Mac

1. **Backend**: abrí http://127.0.0.1:8050/docs → carga Swagger; `GET /health` → `{"status":"ok"}`.
2. **Corpus completo** (debe dar ~50056):
   ```powershell
   .venv\Scripts\python -c "import chromadb; c=chromadb.PersistentClient(path='data/chroma'); print({col.name: c.get_collection(col.name).count() for col in c.list_collections()})"
   ```
3. **Retrieval limpio**: en `/docs`, probá `GET /search` con `q=Microsoft.Data.SqlClient` →
   el primer resultado es **SAC #61037** con título limpio (sin "Recuerda que").
4. **MCP en Claude Code**: comando `/mcp` → `gx-rag` **connected**; probá `search_docs("Postgresql NOWAIT")`.
5. **Frontend**: http://localhost:3050 carga el panel.

Si los 5 dan bien, tenés **exactamente lo mismo que en la Mac**. 🎯

---

## Resumen rapidísimo (para la próxima)

```powershell
git clone -b feat/hybrid-retrieval-and-api-hardening https://github.com/williamgarciadev/gx-rag.git
cd gx-rag
# copiar chroma.zip a .\data\ y:
Expand-Archive .\data\chroma.zip .\data\ -Force
py -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .mcp.json.windows.example .mcp.json
.\dev.ps1
```
