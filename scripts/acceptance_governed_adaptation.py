#!/usr/bin/env python
"""ACCEPTANCE — install a policy, confirm an outcome, watch a weight move.

WHY THIS EXISTS AS A SCRIPT AND NOT AS PROSE
    The run-through's `POST /outcome/confirm` came back 422 naming three
    missing fields, because the script that called it guessed the shape. And
    `POST /governance/envelope` came back 405, because the write route lives
    at `/governance/envelope/confirm` and nothing said so.

    Both are the same defect: the operator had to guess. Prose describing the
    bodies would leave them guessing whether they had transcribed it right.
    This runs the whole flow and prints every request body it sends, so the
    output IS the documentation.

WHAT IT DOES, IN ORDER
    1  GET  /governance/envelope            — read the policy in force
    2  POST /governance/envelope/confirm    — install one (Law 3)
    3  POST /chat                           — a turn, to mint a lineage record
    4  GET  /lineage/recent                 — find it
    5  GET  /belief/{id}/provenance         — the weight BEFORE
    6  POST /outcome/confirm                — the outcome (Law 3)
    7  GET  /belief/{id}/provenance         — the weight AFTER, and why
    8  POST /kairos/cycle                   — seal it into the chain
    9  GET  /chain/status                   — the block that covers it

    Every step prints its request body and the part of the response that
    matters. A step that fails stops the run and says which one.

WHAT IT WRITES
    Governance rows in YOUR store: one envelope version, one outcome event,
    one or more recalibration events, one lineage record from the turn, and a
    chain block. **Nothing is deleted or altered** (I-9) — the envelope is
    additive and a recalibration never touches stored weight. Use
    `--dry-run` to print the bodies and send nothing.

USAGE
    python scripts/acceptance_governed_adaptation.py
    # NEXUS_API_TOKEN comes from .env or the environment; --token overrides
    python scripts/acceptance_governed_adaptation.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import httpx                                                       # noqa: E402

from scripts._console import force_utf8_stdio                      # noqa: E402
from scripts._env import (                                         # noqa: E402
    auth_header, load_env, require_token_or_exit)

# The evidence class the default policy below permits. `extraction` is the
# right first choice: it is the most numerous class in a real store and the
# least trusted, so it is where an outcome signal is worth the most and where
# a mistake costs the least.
MOVABLE_CLASS = "extraction"


def envelope_body(version: str) -> dict:
    return {
        "version":           version,
        "max_delta":         0.05,
        "movable_classes":   [MOVABLE_CLASS],
        "frozen_belief_ids": [],
        "cumulative_max":    0.20,
        "reason":            "acceptance run — bounded trial policy",
        "actor_id":          "operator",
        "confirmed":         True,
    }


def outcome_body(lineage_id: str) -> dict:
    return {
        "event_type":   "review",          # review | qc | agency_response | …
        "decision":     "approved",        # free text; recorded verbatim
        "direction":    "confirming",      # confirming | contradicting | neutral
        "references":   {"lineage_id": lineage_id},
        "magnitude":    0.05,              # sign comes from `direction`
        "workflow_ref": "acceptance-run",
        "notes":        "",
        "actor_id":     "operator",
        "confirmed":    True,              # Law 3 — explicit, no default
    }


class Runner:
    def __init__(self, base: str, token: str, dry: bool):
        self.base, self.dry = base.rstrip("/"), dry
        # U.3 — `auth_header` refuses an empty token rather than letting httpx
        # raise `LocalProtocolError: Illegal header value b'Bearer '`. A
        # script documenting the auth flow must fail on the auth, by name.
        self.h = {**auth_header(token), "Content-Type": "application/json"}
        self.c = httpx.Client(timeout=180.0)
        self.step = 0

    def _say(self, method: str, path: str, body=None) -> None:
        self.step += 1
        print(f"\n[{self.step}] {method} {path}")
        if body is not None:
            print(json.dumps(body, indent=2))

    def get(self, path: str):
        self._say("GET", path)
        if self.dry:
            return {}
        r = self.c.get(self.base + path, headers=self.h)
        if r.status_code != 200:
            raise SystemExit(f"    -> {r.status_code} {r.text[:400]}")
        return r.json()

    def post(self, path: str, body: dict, *, stream: bool = False):
        self._say("POST", path, body)
        if self.dry:
            return {}
        r = self.c.post(self.base + path, headers=self.h, json=body)
        if r.status_code != 200:
            raise SystemExit(f"    -> {r.status_code} {r.text[:400]}")
        return r.text if stream else r.json()


def main() -> int:
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--token", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--version", default="")
    ap.add_argument("--message",
                    default="What is the QT interval and why is it monitored?")
    args = ap.parse_args()

    print(__doc__.split("USAGE")[0].strip())
    print("=" * 72)
    # U.2 — `.env` first, with the same loader the server uses, so a token
    # already in the file needs no --token. U.3 — and if there is none
    # anywhere, stop with a message naming where we looked.
    for path in load_env():
        print(f"  loaded {path}")
    token = args.token if args.dry_run else require_token_or_exit(args.token)
    r = Runner(args.base, token or "dry-run", args.dry_run)
    version = args.version or f"acceptance-{time.strftime('%Y%m%dT%H%M%S')}"

    # 1 --------------------------------------------------------------------
    env = r.get("/governance/envelope")
    if env:
        print(f"    active   : {env['active']['version']} "
              f"(max_delta {env['active']['max_delta']})")
        print(f"    default? : {env['is_default']}   history: {len(env['history'])}")
        print(f"    install  : {env['confirm']['method']} "
              f"{env['confirm']['endpoint']}")

    # 2 --------------------------------------------------------------------
    got = r.post("/governance/envelope/confirm", envelope_body(version))
    if got:
        print(f"    -> installed {got['version']}  hash {got['record_hash'][:12]}…")

    after = r.get("/governance/envelope")
    if after:
        print(f"    active   : {after['active']['version']}   "
              f"history: {len(after['history'])}")
        if after["is_default"]:
            raise SystemExit("    the default is still in force — install failed")

    # 3 --------------------------------------------------------------------
    r.post("/chat", {"message": args.message}, stream=True)
    print("    -> turn complete")

    # 4 --------------------------------------------------------------------
    recent = r.get("/lineage/recent?limit=1")
    lineage_id = None
    if recent:
        records = recent.get("records") or []
        if not records:
            raise SystemExit("    no lineage record — did the turn inject "
                             "any beliefs? An answer that cited nothing has "
                             "nothing for an outcome to attach to.")
        lineage_id = records[0]["lineage_id"]
        print(f"    -> lineage {lineage_id}")

    # 5 --------------------------------------------------------------------
    detail = r.get(f"/lineage/{lineage_id}") if lineage_id else {}
    belief_id = None
    if detail:
        movable = [i for i in detail["injected"]
                   if i.get("resolved") and i.get("source") == MOVABLE_CLASS
                   and i.get("cited") is not False]
        print(f"    injected {len(detail['injected'])}, "
              f"{len(movable)} movable under this policy")
        print(f"    coverage : {detail['provenance_coverage']['note'][:90]}…")
        if not movable:
            raise SystemExit(
                f"    no cited belief of class {MOVABLE_CLASS!r} was injected. "
                f"The outcome would be recorded and refused, which is correct "
                f"but proves nothing. Ask something your store answers from "
                f"extraction-sourced beliefs, or widen movable_classes.")
        belief_id = movable[0]["belief_id"]
        before = r.get(f"/belief/{belief_id}/provenance")
        print(f"    belief {belief_id}: stored {before['stored_weight']} "
              f"effective {before['effective_weight']} "
              f"steps {len(before['steps'])}")

    # 6 --------------------------------------------------------------------
    out = r.post("/outcome/confirm", outcome_body(lineage_id or "<lineage_id>"))
    if out:
        print(f"    -> applied {len(out['applied'])}, refused {len(out['refused'])}")
        for a in out["applied"]:
            print(f"       belief {a['belief_id']}: {a['prominence_before']:.4f} "
                  f"-> {a['prominence_after']:.4f}  (envelope "
                  f"{a['envelope_version']})")
        for d in out["refused"]:
            print(f"       belief {d['belief_id']}: REFUSED {d['rule']} — "
                  f"{d['detail']}")

    # 7 --------------------------------------------------------------------
    if belief_id is not None:
        after_p = r.get(f"/belief/{belief_id}/provenance")
        print(f"    stored {after_p['stored_weight']} (unchanged — I-8), "
              f"effective {after_p['effective_weight']}")
        for s in after_p["steps"]:
            print(f"       {s['delta']:+.4f} by {s['actor']['id']} under "
                  f"{s['envelope_version']} <- outcome {s['outcome_ref']} "
                  f"(sealed: {s['sealed']})")

    # 8, 9 -----------------------------------------------------------------
    cyc = r.post("/kairos/cycle", {})
    if cyc:
        # U.1 — `sealed_records`, not `governance_sealed_records`. This call
        # forces a snapshot by default, the snapshot path seals whatever is
        # pending, and the governance pass then correctly finds nothing left.
        # Reading the cadence field made the script report "0 record(s)" for a
        # cycle that had just sealed three.
        print(f"    -> sealed {cyc.get('sealed_records')} record(s) into "
              f"{cyc.get('sealed_block_kind') or 'no'} block "
              f"{cyc.get('sealed_block_index')}")
        if not cyc.get("sealed_records"):
            print(f"       (nothing was pending: "
                  f"{cyc.get('governance_skipped_reason') or 'already sealed'})")
    chain = r.get("/chain/status")
    if chain:
        print(f"    chain: {chain}")

    if belief_id is not None and not args.dry_run:
        final = r.get(f"/belief/{belief_id}/provenance")
        sealed = [s for s in final["steps"] if s["sealed"]]
        print(f"\n{len(sealed)}/{len(final['steps'])} recalibration(s) sealed "
              f"into the chain.")

    print("\nDone. Every body above is the exact shape the endpoint accepts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
