"""W10c (run/specs/W10c.md S1) — the replay capture's two switches, with no provider behind them.

`NEXUS_INFERENCE_SEED` and `NEXUS_KEEP_REQUESTS` and their readers, moved here verbatim from
`backend/providers/replay_capture.py`, which re-exports the same objects, so every caller is unchanged. They moved
because the lineage store reads them at its record write, and importing them from the providers' package ran that
package (every provider, and through the providers' base the loop and the cortex): a recorder with no memory engine
(the interceptor, `backend/intercept/`) could not write a record without loading one. Standard library only.
"""
from __future__ import annotations

import os

SEED_SETTING = "NEXUS_INFERENCE_SEED"
KEEP_SETTING = "NEXUS_KEEP_REQUESTS"


def seed_enabled() -> bool:
    """`NEXUS_INFERENCE_SEED=off` sends no seed (the pre-W0b request). Read at
    call time."""
    return os.environ.get(SEED_SETTING, "on").strip().lower() != "off"


def keep_requests_enabled() -> bool:
    """`NEXUS_KEEP_REQUESTS=off` keeps no request bytes. On by default: a
    record whose request is not kept can never be re-run. Read at call time."""
    return os.environ.get(KEEP_SETTING, "on").strip().lower() != "off"
