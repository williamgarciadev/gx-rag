"""Every data path must be absolute and independent of the caller's cwd.

The MCP server runs under the client's working directory (whatever project the
user is in), so a relative default silently points at the wrong place: an empty
ChromaDB, a missing syntax catalog, a stray data/ dir next to the caller.
"""

import os

import pytest

pytestmark = pytest.mark.unit

from src import paths


def _all_paths() -> dict:
    return {
        "REPO_ROOT": paths.REPO_ROOT,
        "DATA_DIR": paths.DATA_DIR,
        "CHROMA_PATH": paths.CHROMA_PATH,
        "SYNTAX_DB_PATH": paths.SYNTAX_DB_PATH,
        "SCRAPED_DIR": paths.SCRAPED_DIR,
    }


def test_all_data_paths_are_absolute():
    for name, path in _all_paths().items():
        assert path.is_absolute(), f"{name} is relative: {path}"


def test_paths_do_not_move_when_cwd_changes(tmp_path):
    before = _all_paths()

    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        after = {k: v for k, v in _all_paths().items()}
    finally:
        os.chdir(cwd)

    assert before == after


def test_data_paths_live_under_the_repo_data_dir():
    assert (paths.REPO_ROOT / "src" / "vectorstore").is_dir()
    assert paths.DATA_DIR == paths.REPO_ROOT / "data"
    for path in (paths.CHROMA_PATH, paths.SYNTAX_DB_PATH, paths.SCRAPED_DIR):
        assert path.parent == paths.DATA_DIR
