"""The simulated UAT's stand-in for the tombstone route W14 decides (W8-B3: no route reaches
`LineageStore.tombstone`; docs/OPERATOR_RUNBOOK.md says so). `scripts/uat_simulate.py`'s verification section
runs it as a child on a restored copy of the console's store that nothing serves: the one act of the run not
reached through the product's own entry points, and its report row says so. Not an operator tool, and not
collected (the name starts with an underscore). It opens the store with the store lock, as every process does,
so a store a console is serving is refused with the lock's own exit code.

    python tests/_uat_tombstone.py --data-root DIR --lineage-id ID --actor ID --reason TEXT

Its first line names the store it opened (law 15's form); its last line is the act, as JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data-root", required=True, type=Path)
    ap.add_argument("--lineage-id", required=True)
    ap.add_argument("--actor", required=True)
    ap.add_argument("--reason", required=True)
    args = ap.parse_args(argv)
    root = args.data_root.resolve()
    os.environ["NEXUS_DATA_ROOT"] = str(root)            # every side file the services write lands under the copy
    sys.path.insert(0, str(ROOT))
    from backend.data_root import paths_under
    from backend.server import create_services
    from backend.store_lock import EXIT_STORE_LOCKED, StoreLocked
    print(f"data root: {root}", flush=True)
    try:
        services = create_services(**paths_under(root))
    except StoreLocked as e:
        print(f"tombstone: REFUSED: the store is held by another process ({e})", flush=True)
        return EXIT_STORE_LOCKED
    try:
        act = services.lineage.tombstone(args.lineage_id, chain=services.archive_chain,
                                         actor={"id": args.actor}, reason=args.reason)
    finally:
        services.store_lock.release()
    print(json.dumps(act, sort_keys=True, default=str), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
