"""Console output that survives a non-UTF-8 terminal.

Windows consoles default to cp1252, which cannot encode the arrows, em-dashes
and accents these CLIs print — the process dies with UnicodeEncodeError on the
final summary line, *after* the real work is done. That failure mode is worse
than ugly output: it looks like the job crashed when it actually succeeded.
"""

import sys


def use_utf8_stdout() -> None:
    """Force UTF-8 on stdout/stderr, degrading unencodable chars instead of raising."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
