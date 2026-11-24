"""W8 — a record's inclusion proof, built from the store and verified from itself alone (run/specs/W8.md, D7).

WHAT A PROOF CLAIMS
    This leaf is in this block's root, and this block's hash is this header's
    (or, for a block sealed before W8, this body's). Nothing about the content
    beyond the openings a credential presents (W9), and nothing about the
    chain from this block to its head: W9 signs the head.

TWO SHAPES
    layout 2 (a block appended since W8): the leaf is `record_hash ||
        commitment_root`; the header carries the lineage root and its size,
        so the proof is the header, the leaf, its index and its audit path.
    layout 1 (every block before W8): the block's hash covers its whole
        fingerprint, so the proof carries the block's body; the leaf is the
        record hash, in the block's sealed list, under the root derived from
        that list. Once a later seal has bound the record (D6), the proof
        also carries that binding: the leaf `record_hash || commitment_root`
        in the binding block's root. A binding claims that this instance
        bound these commitments to this record's hash at that block's time;
        it cannot claim the record held the committed values when it was
        first sealed, which would need the unsalted body of then.

WHAT `verify_proof` IMPORTS
    `hashlib`, `json` and `backend.memory.merkle` (itself `hashlib` only), so
    W9's standalone verifier can carry the two files unchanged.
"""
from __future__ import annotations

import hashlib
import json

from backend.memory import merkle

PROOF_VERSION = 1
BINDING_REFUSALS = ("binding_malformed", "binding_block_hash_mismatch", "binding_size_mismatch",
                    "binding_leaf_mismatch", *("binding_" + r for r in merkle.REFUSALS))
REFUSALS = ("malformed", "block_hash_mismatch", "size_mismatch", "leaf_mismatch", "not_in_list",
            *merkle.REFUSALS, *BINDING_REFUSALS)


def _is_hash(value) -> bool:
    """A SHA-256 hex digest: 64 lower-case hex characters."""
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _canonical_hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _layout_1_hash(body: dict) -> str:
    """A layout-1 block's hash, as `ArchiveBlock.compute_hash` builds it for
    every block before W8 (repeated here so a verifier needs no store code)."""
    hashed = {k: body[k] for k in ("index", "timestamp", "fingerprint", "snapshot_path", "snapshot_hash",
                                   "previous_hash")}
    if body.get("snapshot_missing"):
        hashed["snapshot_missing"] = True
    if body.get("kind", "snapshot") != "snapshot":
        hashed["kind"] = body["kind"]
    return _canonical_hash(hashed)


def build_proof(lineage, chain, lineage_id: str) -> dict | None:
    """The proof for one record, or None when no such record exists. An
    unsealed record has no proof yet, and says so."""
    row = lineage.conn.execute("SELECT record_hash, sealed_block FROM lineage_records WHERE lineage_id = ?",
                               (lineage_id,)).fetchone()
    if row is None:
        return None
    out = {"proof_version": PROOF_VERSION, "lineage_id": lineage_id, "record_hash": row["record_hash"]}
    if row["sealed_block"] is None:
        return {**out, "state": "unsealed"}
    block = chain.get_block(int(row["sealed_block"]))
    if block is None:
        return {**out, "state": "block_missing", "sealed_block": int(row["sealed_block"])}
    seal = (block.fingerprint or {}).get("lineage_seal") or {}
    hashes = list(seal.get("record_hashes") or [])
    if block.layout != 1:
        # The leaf is made from the STORE's commitment root, never read off the
        # sealed leaf: a root rewritten in the store then matches no sealed
        # leaf, and the proof says so (W8's verifier, MF1).
        leaves = list(seal.get("leaves") or hashes)
        root = _store_root(lineage, lineage_id)
        if root and row["record_hash"] + root in leaves:
            leaf = row["record_hash"] + root
        elif row["record_hash"] in leaves:
            # Sealed as its bare hash (it had no root then): the seal covers
            # no root, so the proof claims none; a root made later is
            # anchored only by a binding, carried below when there is one.
            leaf, root = row["record_hash"], None
        else:
            return {**out, "state": "not_in_block", "sealed_block": block.index}
        index = leaves.index(leaf)
        path = merkle.audit_path([bytes.fromhex(x) for x in leaves], index)
        proof = {**out, "state": "sealed", "commitment_root": root,
                "leaf": leaf, "leaf_index": index, "tree_size": len(leaves), "path": [p.hex() for p in path],
                "seal": "lineage",
                "block": {"layout": block.layout, "index": block.index, "block_hash": block.block_hash,
                          "header": block.header()}}
        if root is None:
            binding = _binding(lineage, chain, lineage_id, row["record_hash"])
            if binding is not None:
                proof["binding"] = binding
        return proof
    if row["record_hash"] not in hashes:
        return {**out, "state": "not_in_block", "sealed_block": block.index}
    index = hashes.index(row["record_hash"])
    path = merkle.audit_path([bytes.fromhex(h) for h in hashes], index)
    proof = {**out, "state": "sealed", "commitment_root": None, "leaf": row["record_hash"], "leaf_index": index,
             "tree_size": len(hashes), "path": [p.hex() for p in path], "seal": "lineage",
             "block": {"layout": 1, "index": block.index, "block_hash": block.block_hash,
                       "body": {k: v for k, v in block.to_dict().items() if k != "block_hash"}}}
    binding = _binding(lineage, chain, lineage_id, row["record_hash"])
    if binding is not None:
        proof["binding"] = binding
    return proof


def _store_root(lineage, lineage_id: str) -> str | None:
    try:
        row = lineage.conn.execute("SELECT commitment_root FROM record_commitments WHERE lineage_id = ?",
                                   (lineage_id,)).fetchone()
    except Exception:                                       # noqa: BLE001 - a store before W8
        return None
    return None if row is None else row["commitment_root"]


def _binding(lineage, chain, lineage_id: str, record_hash: str) -> dict | None:
    """D6 — the binding of a record sealed before W8, or None while no seal
    has bound it (or the store keeps no commitments)."""
    try:
        row = lineage.conn.execute("SELECT commitment_root, bound_block FROM record_commitments "
                                   "WHERE lineage_id = ?", (lineage_id,)).fetchone()
    except Exception:                                       # noqa: BLE001 - a store before W8
        return None
    if row is None or row["bound_block"] is None:
        return None
    block = chain.get_block(int(row["bound_block"]))
    if block is None:
        return {"state": "block_missing", "bound_block": int(row["bound_block"])}
    leaves = list(((block.fingerprint or {}).get("binding_seal") or {}).get("leaves") or [])
    leaf = record_hash + row["commitment_root"]
    if leaf not in leaves:
        return {"state": "not_in_block", "bound_block": block.index}
    index = leaves.index(leaf)
    path = merkle.audit_path([bytes.fromhex(x) for x in leaves], index)
    return {"state": "bound", "commitment_root": row["commitment_root"], "leaf": leaf, "leaf_index": index,
            "tree_size": len(leaves), "path": [p.hex() for p in path],
            "block": {"layout": block.layout, "index": block.index, "block_hash": block.block_hash,
                      "header": block.header()}}


def _verify_binding(binding: dict, record_hash: str) -> str | None:
    """None when a binding holds; otherwise why not, as one of `BINDING_REFUSALS`."""
    if binding.get("state") != "bound":
        return f"binding_malformed: a binding that is {binding.get('state')!r} binds nothing"
    block, header = binding["block"], binding["block"]["header"]
    if _canonical_hash(header) != block["block_hash"] or header.get("index") != block["index"]:
        return "binding_block_hash_mismatch: the binding's header does not hash to its block's hash"
    entry = header["roots"]["binding"]
    if binding["tree_size"] != entry["size"]:
        return "binding_size_mismatch: the binding tree's size is not the size the header seals"
    if binding["leaf"] != record_hash + binding["commitment_root"]:
        return "binding_leaf_mismatch: the binding's leaf is not this record's hash and commitment root"
    why = merkle.verify_inclusion(bytes.fromhex(binding["leaf"]), int(binding["leaf_index"]),
                                  int(binding["tree_size"]), [bytes.fromhex(x) for x in binding["path"]],
                                  bytes.fromhex(entry["root"]))
    return None if why is None else f"binding_{why}"


def verify_proof(proof: dict) -> str | None:
    """None when the proof holds; otherwise why not, as one of `REFUSALS`."""
    try:
        if proof.get("state") != "sealed":
            return f"malformed: a proof of a {proof.get('state')!r} record proves nothing"
        block, leaf_hex = proof["block"], proof["leaf"]
        if not _is_hash(proof["record_hash"]) or leaf_hex != proof["record_hash"] + (proof.get("commitment_root") or ""):
            return "leaf_mismatch: the leaf is not this record's hash and commitment root"
        leaf, path = bytes.fromhex(leaf_hex), [bytes.fromhex(p) for p in proof["path"]]
        if block["layout"] != 1:
            header = block["header"]
            if _canonical_hash(header) != block["block_hash"] or header.get("index") != block["index"]:
                return "block_hash_mismatch: the header does not hash to the block's hash"
            entry = header["roots"]["lineage"]
            if proof["tree_size"] != entry["size"]:
                return "size_mismatch: the tree's size is not the size the header seals"
            root = bytes.fromhex(entry["root"])
        else:
            body = block["body"]
            if _layout_1_hash(body) != block["block_hash"] or body.get("index") != block["index"]:
                return "block_hash_mismatch: the body does not hash to the block's hash"
            hashes = body["fingerprint"]["lineage_seal"]["record_hashes"]
            if proof["record_hash"] not in hashes:
                return "not_in_list: the record's hash is not in the block's sealed list"

            if proof["tree_size"] != len(hashes):
                return "size_mismatch: the tree's size is not the list's"
            root = merkle.root([bytes.fromhex(h) for h in hashes])
        why = merkle.verify_inclusion(leaf, int(proof["leaf_index"]), int(proof["tree_size"]), path, root)
        if why is None and "binding" in proof:
            why = _verify_binding(proof["binding"], proof["record_hash"])
        return why
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        return f"malformed: {type(e).__name__}"
