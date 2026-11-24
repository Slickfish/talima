"""Packet M.4 — make every script's stdout/stderr UTF-8, explicitly.

THE MEASURED DEFECT
    On the operator's Windows machine, a test capturing a script's output
    died in the child-reader thread:

        UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97 in position 90
          in subprocess.py _readerthread  ->  captured output is None

    `0x97` is the cp1252 em dash. Every script in this repo prints em dashes,
    arrows and section rules. When a script's stdout is a PIPE rather than a
    console, CPython encodes it with `locale.getpreferredencoding()` — cp1252
    on a UK/US Windows install — so `print("—")` writes the single byte 0x97
    without raising. The parent then decodes that pipe as UTF-8 and the
    reader thread dies. `subprocess.run` swallows the thread's exception,
    `proc.stdout` comes back `None`, and eight assertions fail with
    `TypeError` for a reason that has nothing to do with what they assert.

WHY BOTH ENDS
    M.4 fixes the reader too (`errors="replace"` on every capture), and that
    alone would stop the crash. It is not enough on its own: `replace` turns
    the em dash into U+FFFD, so a test asserting on the text would see
    mangled output and an operator reading the console would see mojibake.
    Fixing the WRITER makes the bytes correct; fixing the READER makes a
    future mistake non-fatal. Neither substitutes for the other.

WHY NOT PYTHONIOENCODING
    It works, but it lives in the environment rather than the repo — the
    operator would have to remember it, on every machine, forever. Pack §4:
    *a documented rule is not a control.* This is the control, and
    `tests/test_subprocess_encoding.py` asserts every script calls it.
"""
from __future__ import annotations

import sys


def force_utf8_stdio() -> None:
    """Re-encode this process's stdout/stderr as UTF-8. Idempotent, and safe
    to call when the streams are pipes, files, consoles, or pytest's captured
    replacements.

    `errors="replace"` on the WRITE side too: a script's job is to report a
    measurement, and it must not die part-way through printing it because one
    character could not be encoded. A visible U+FFFD is a far better outcome
    than a truncated report and a traceback — that is the same reasoning F.2
    applied to an unreadable tier, and Law 8's "declare, do not fail silently"
    applied to output.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            # Not a TextIOWrapper — pytest's capture object, a StringIO, or a
            # detached stream. Nothing to do, and nothing to complain about.
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # Already detached, or a stream that refuses reconfiguration.
            # Never fatal: this function exists to make output readable, and
            # failing to make it readable must not stop the work.
            pass
