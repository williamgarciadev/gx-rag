"""The store must resolve to the same directory no matter the caller's cwd.

The MCP server is launched by clients (Claude Code) whose working directory is
whatever project the user is in — not this repo. A cwd-relative default would
silently open an empty store there instead of the real corpus.
"""

import os

from src.vectorstore.chroma_client import default_persist_path


def test_default_path_is_absolute_and_cwd_independent(tmp_path):
    before = default_persist_path()

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        after = default_persist_path()
    finally:
        os.chdir(cwd)

    assert before.is_absolute()
    assert before == after


def test_default_path_points_at_the_repo_data_dir():
    path = default_persist_path()

    assert path.name == "chroma"
    assert path.parent.name == "data"
    assert (path.parent.parent / "src" / "vectorstore").is_dir()
