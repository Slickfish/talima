"""W10c (run/specs/W10c.md S1) — the governance seal, out of KAIROS: what a governance block seals, and one pass that
appends it.

Moved, not rewritten. `seal_payload` is KAIROS's `_seal_payload` line for line (S.2b's one builder); the reads and
marks are KAIROS's helpers' bodies, with their never-raise rule. KAIROS delegates to this module and keeps its own
gate, cadence and state (`maybe_seal_governance`), so the console and MCP seal exactly as before; `seal_governance`
is the pass a process with no memory engine runs (the interceptor, `backend/intercept/`): read what is unsealed,
append one `governance` block, mark what it covers. This module imports nothing from the knowledge store, the cortex
or the loop (tests/test_w10c_standalone.py).

The recalibration digest is `LineageStore.seal_digest`: `RecalibrationStore.seal_digest` is the same construction,
deliberately (its docstring says so), and tests/test_w10c_standalone.py pins the two equal.
"""
from __future__ import annotations

import logging

from backend.memory.lineage import LineageStore

logger = logging.getLogger(__name__)

BINDING_BATCH = 2000                             # PROVISIONAL: a size, fitted to nothing (moved from KAIROS)


def seal_payload(lineage_unsealed: list, recal_unsealed: list, lineage_leaves: list | None = None,
                 bindings: list | None = None, held: list | None = None, acts: list | None = None) -> dict:
    """The seal keys for one block. One builder, used by every path.

    `lineage_seal` and `recalibration_seal` stay in the SAME block and are
    not split. They are the same kind of thing — per-decision governance
    records — and a recalibration cites the lineage record whose answer
    produced the outcome. Sealing them into different blocks would let a
    verifier hold one and not the other, which is a worse property than
    the block volume it would save.
    """
    payload: dict = {}
    if lineage_unsealed:
        hashes = sorted(h for _lid, h in lineage_unsealed)
        payload["lineage_seal"] = {
            "count":         len(hashes),
            "digest":        LineageStore.seal_digest(hashes),
            "record_hashes": hashes,
        }
        # W8 (D5) — each record's leaf, `record_hash || commitment_root`,
        # in the same order: the block's lineage root is over these.
        if lineage_leaves is not None:
            payload["lineage_seal"]["leaves"] = list(lineage_leaves)
    if recal_unsealed:
        rhashes = sorted(h for _k, h in recal_unsealed)
        payload["recalibration_seal"] = {
            "count":         len(rhashes),
            "digest":        LineageStore.seal_digest(rhashes),
            "record_hashes": rhashes,
        }
    # W8 (D6) — the binding seal: each record whose seal claims no root bound to its
    # commitment root (leaf `record_hash || commitment_root`), with the
    # block that first sealed it. It claims that this instance bound these
    # commitments to that hash at this block's time; not that the record
    # held these values when it was first sealed.
    if bindings:
        entries = sorted(bindings, key=lambda b: (b[1], b[0]))
        bhashes = [h for _lid, h, _root, _first in entries]
        payload["binding_seal"] = {
            "count":         len(entries),
            "digest":        LineageStore.seal_digest(bhashes),
            "record_hashes": bhashes,
            "leaves":        [h + root for _lid, h, root, _first in entries],
            "first_sealed":  [first for _lid, _h, _root, first in entries],
        }
    # W9 (D8) — the held commitments: each commitment a leaf of the block's
    # `commit` root, beside its id. The content and salt never leave.
    if held:
        payload["commit_seal"] = {
            "count":          len(held),
            "commitment_ids": [cid for cid, _c in held],
            "leaves":         [c for _cid, c in held],
        }
    # W10b (D6) — the governed actions whose outcome is written: each
    # action's record hash a leaf of the block's `action` root, beside its id.
    if acts:
        payload["action_seal"] = {
            "count":      len(acts),
            "action_ids": [aid for aid, _h in acts],
            "leaves":     [h for _aid, h in acts],
        }
    return payload


# ---------- the reads (never raise where KAIROS's never did) ----------

def unsealed_lineage(lineage) -> list:
    return lineage.unsealed() if lineage is not None else []


def unsealed_recalibrations(recalibration) -> list:
    return recalibration.unsealed() if recalibration is not None else []


def unsealed_commitments(lineage) -> list:
    """W9 (D8) — never raises: a seal that cannot read them seals the rest."""
    if lineage is None or not hasattr(lineage, "unsealed_commitments"):
        return []
    try:
        return lineage.unsealed_commitments()
    except Exception as e:
        logger.warning("held commitments could not be read for the seal: %s: %s", type(e).__name__, e)
        return []


def unsealed_actions(lineage) -> list:
    """W10b (D6) — never raises: a seal that cannot read them seals the rest."""
    if lineage is None or not hasattr(lineage, "unsealed_actions"):
        return []
    try:
        return lineage.unsealed_actions()
    except Exception as e:
        logger.warning("governed actions could not be read for the seal: %s: %s", type(e).__name__, e)
        return []


def unbound_lineage(lineage, chain, *, limit: int) -> list:
    """W8 (D6) — the records whose seal claims no root (sealed before W8,
    or a layout-2 seal of the bare hash) and whose root, rebuilt from
    their values, no binding has sealed yet, at most `limit`; [] with no store or no chain. Never raises:
    a seal that cannot read them still seals everything else."""
    if lineage is None or chain is None or not hasattr(lineage, "unbound"):
        return []
    try:
        return lineage.unbound(chain, limit=limit)
    except Exception as e:
        logger.warning("the seal could not read the records awaiting a binding: %s: %s", type(e).__name__, e)
        return []


def lineage_leaves(lineage, lineage_unsealed: list) -> list | None:
    """W8 — the seal's leaves, from the lineage store; None with no store."""
    if not lineage_unsealed or lineage is None or not hasattr(lineage, "seal_leaves"):
        return None
    return lineage.seal_leaves(lineage_unsealed)


# ---------- the marks ----------

def mark_sealed(lineage, recalibration, lineage_unsealed: list, recal_unsealed: list, block_index: int) -> int:
    n = 0
    if lineage_unsealed and lineage is not None:
        n += lineage.mark_sealed([lid for lid, _h in lineage_unsealed], block_index)
    if recal_unsealed and recalibration is not None:
        n += recalibration.mark_sealed([k for k, _h in recal_unsealed], block_index)
    return n


def mark_bound(lineage, bindings: list, block_index: int) -> int:
    if not bindings or lineage is None:
        return 0
    return lineage.mark_bound([lid for lid, _h, _root, _first in bindings], block_index)


def mark_commitments_sealed(lineage, held: list, block_index: int) -> int:
    if not held or lineage is None:
        return 0
    return lineage.mark_commitments_sealed([cid for cid, _c in held], block_index)


def mark_actions_sealed(lineage, acts: list, block_index: int) -> int:
    if not acts or lineage is None:
        return 0
    return lineage.mark_actions_sealed([aid for aid, _h in acts], block_index)


# ---------- the standalone pass ----------

def seal_governance(chain, lineage, recalibration=None, *, binding_batch: "int | None" = None):
    """Append one GOVERNANCE block over everything unsealed and mark it; None when nothing is pending.

    KAIROS's `maybe_seal_governance` without its gate, its cadence or its state: the caller decides when. It raises
    what the append or a mark raises (the interceptor logs it and tries again on its next tick); a read that fails
    seals the rest, as in KAIROS."""
    lineage_unsealed = unsealed_lineage(lineage)
    recal_unsealed = unsealed_recalibrations(recalibration)
    bindings = unbound_lineage(lineage, chain, limit=BINDING_BATCH if binding_batch is None else binding_batch)
    held = unsealed_commitments(lineage)
    acts = unsealed_actions(lineage)
    if not (lineage_unsealed or recal_unsealed or bindings or held or acts):
        return None
    payload = {"kind": "governance"}
    payload.update(seal_payload(lineage_unsealed, recal_unsealed, lineage_leaves(lineage, lineage_unsealed),
                                bindings, held, acts))
    # The block is appended in one store and the marks written in another, so a pass can append and then fail to
    # mark. The next pass then finds this very seal at the chain's head: it marks that block and appends nothing.
    head = chain.latest()
    seals = {key: value for key, value in payload.items() if key != "kind"}
    if head is not None and head.kind == "governance" \
            and {key: (head.fingerprint or {}).get(key) for key in seals} == seals \
            and not [key for key in (head.fingerprint or {}) if key.endswith("_seal") and key not in seals
                     and key != "identity_seal"]:
        block = head
    else:
        block = chain.append(payload, "", kind="governance")
    mark_sealed(lineage, recalibration, lineage_unsealed, recal_unsealed, block.index)
    mark_bound(lineage, bindings, block.index)
    mark_commitments_sealed(lineage, held, block.index)
    mark_actions_sealed(lineage, acts, block.index)
    return block
