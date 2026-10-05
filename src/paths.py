"""Canonical, cwd-independent locations of the project's data stores.

The MCP server is launched by clients (Claude Code, Claude Desktop) whose
working directory is whatever project the user happens to be in. Anything
resolved relative to the cwd would therefore point somewhere else on every
invocation — an empty ChromaDB, a missing syntax catalog. Anchor to the
package instead.

Under Docker the repo is the WORKDIR, so these resolve to the same bind-mounted
paths as before.
"""

from pathlib import Path

# paths.py -> src/ -> repo root
REPO_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = REPO_ROOT / "data"
CHROMA_PATH = DATA_DIR / "chroma"
SYNTAX_DB_PATH = DATA_DIR / "syntax.db"
SCRAPED_DIR = DATA_DIR / "scraped"
