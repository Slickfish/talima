"""SNIP-DET child process: two identical CE rigs in one interpreter, rows out
as one JSON line. `tests/test_snip_determinism.py` runs this under several
PYTHONHASHSEED values and requires every run's rows — within the process
and across seeds — to be identical.

Plain python, not pytest: conftest's redirects do not apply, so every path
the rig writes is set here to a fresh temp directory and nothing reaches
`data/`.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    turns = int(sys.argv[1]) if len(sys.argv) > 1 else 12
    tmp = Path(tempfile.mkdtemp(prefix="snipdet_"))
    os.environ.setdefault("NEXUS_API_TOKEN", "snipdet")
    os.environ["NEXUS_CALIBRATION_LOG"] = str(tmp / "cal.jsonl")
    os.environ["NEXUS_FIRE_CAPTURE_PATH"] = str(tmp / "fire.jsonl")
    os.environ["NEXUS_CITATION_CAPTURE_PATH"] = str(tmp / "cap.jsonl")
    from tests.fixtures.ce_rig import CERig
    runs = []
    for i in (1, 2):
        rig = CERig(tmp / f"r{i}", pairs=20, answer_chars=800, kb_lines=17, user_chars=76)
        runs.append([list(r.as_tuple()) for r in rig.run(turns)])
        rig.close()
    print(json.dumps({"seed": os.environ.get("PYTHONHASHSEED"), "platform": sys.platform, "runs": runs}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
