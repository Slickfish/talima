"""The simulated UAT's "another program": it opens a store's file read-only and holds one read open.

`scripts/uat_simulate.py`'s interceptor section runs it as a child for its forced-failure step. SQLite lets no
writer commit under an open read, so while this holds, the interceptor cannot record a request and must refuse it
before sending (docs/INTERCEPTOR.md, "When something goes wrong"). The UAT's runner opens no store itself
(tests/test_w4_uat_simulate.py D20); this is the program that does, on the UAT's own scratch store and never on an
instance's. Not an operator tool, and not collected (the name starts with an underscore).

    python tests/_uat_hold_read.py --db FILE [--seconds N]

It prints `holding` once the read is open and `released` when it has closed it: when it is stopped, or after N
seconds (default 120).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--db", required=True, type=Path)
    ap.add_argument("--seconds", type=float, default=120.0)
    args = ap.parse_args(argv)
    conn = sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True)
    reading = conn.execute("SELECT name FROM sqlite_master")
    try:
        reading.fetchone()                           # one row taken, the read left open
        print("holding", flush=True)
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            time.sleep(0.05)
    except KeyboardInterrupt:                        # the runner's stop
        pass
    finally:
        reading.close()                              # the read ends here; then the connection
        conn.close()
    print("released", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
