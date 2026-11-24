"""W9 (run/specs/W9.md D5) — a decision credential: one record, provable by a stranger offline.

A credential carries the record's commitment root, every field's salted
commitment, the fields the holder chose to disclose with their values and
salts, W8's proof that the record's leaf is in its block (and the binding,
when the seal claims no root), the instance's signature over each such block,
the instance's key list, the record's scope and its declaration.
`scripts/nexus_verify.py` checks it with the standard library alone.

Disclosure is explicit and salted: a field appears with its value only when
it is named; every other field appears only as its salted commitment. The
record hash rides inside the leaf as an opaque value (the body it hashes holds
a random 128-bit id, so it cannot be tested against guessed content) and is
never offered as something to recompute. A tombstoned record discloses only
what it kept. A block sealed before W9 carries no instance signature, so the
credential's signature for it is made when the credential is issued and says
so (`signed: issue`); a block signed when appended says `seal`. So is a
block appended unsigned (the key could not be read), by the current key.
"""
from __future__ import annotations

from backend.mcp.scope import declaration
from backend.memory import commitments as _commitments
from backend.memory import instance_key as _ik
from backend.memory import proofs as _proofs
from backend.memory.lineage import LineageStore, _hash_payload_from_row

CREDENTIAL = "nexus.credential"
VERSION = 1
DECISION_FIELDS = ("envelope_version", "forecast")


class CredentialRefused(ValueError):
    """`<reason>: <why>`; nothing was issued."""


def _signature(block, key) -> dict:
    """The block's own signature, recorded beside it (`seal`), or, for a
    block that has none (appended before W9, or while the key could not be
    read), one made now by the current key (`issue`), which says so. The
    current key's range will always cover a block that exists now: the next
    key's range starts where it is made (W9's delta check 3)."""
    if block.instance_signature:
        return {"block_index": block.index, "block_hash": block.block_hash, "key_id": block.instance_key_id,
                "value": block.instance_signature, "signed": "seal"}
    return {"block_index": block.index, "block_hash": block.block_hash, "key_id": key.key_id,
            "value": key.sign(_ik.block_statement(block.index, block.block_hash)).hex(), "signed": "issue"}


RECEIPT = "nexus.commitment"


def commitment_receipt(lineage, chain, commitment_id: str, *, key_path) -> dict:
    """W9 (D8) — what a person keeps beside the content and salt to reveal a
    held commitment later: the commitment, its leaf's path to the block's
    `commit` root, the block's header and signature, and the key list. No
    content and no salt: those the person holds, and reveals when they choose."""
    from backend.memory import merkle
    row = lineage.held_commitment(commitment_id)
    if row is None:
        raise CredentialRefused("no_commitment: no held commitment has this id")
    if row["sealed_block"] is None:
        raise CredentialRefused("unsealed: the commitment is sealed by the next seal; ask again after it")
    block = chain.get_block(int(row["sealed_block"]))
    seal = ((block.fingerprint or {}).get("commit_seal") or {}) if block is not None else {}
    leaves, ids = seal.get("leaves") or [], seal.get("commitment_ids") or []
    if commitment_id not in ids or leaves[ids.index(commitment_id)] != row["commitment"]:
        raise CredentialRefused("does_not_verify: the block that sealed the commitment does not hold it")
    index = ids.index(commitment_id)
    path = merkle.audit_path([bytes.fromhex(c) for c in leaves], index)
    key = _ik.load_or_create(key_path, chain=chain)
    keys = _ik.identity_document(key, chain)["keys"]
    signature = _signature(block, key)
    return {"receipt": RECEIPT, "version": VERSION, "commitment_id": commitment_id,
            "commitment": row["commitment"], "held_at": row["ts"], "leaf_index": index, "tree_size": len(leaves),
            "path": [p.hex() for p in path],
            "block": {"layout": block.layout, "index": block.index, "block_hash": block.block_hash,
                      "header": block.header()},
            "signatures": [signature], "keys": keys,
            "anchors": [a for a in [chain.anchor_for(block.index)] if a]}


ACTION_RECEIPT = "nexus.action"
# W10b (D7) — the receipt names the person accountable for the action, so it is a per-person surface (A-2).
ACTION_PER_PERSON = {
    "declared": True,
    "field": "disclosed.accountable_identity",
    "note": ("a per-person record: the action names the identity accountable for it, as the request carried it "
             "(fields.identity_mechanism says how: asserted, recorded as stated and never authenticated, or "
             "proxy, a loopback identity proxy's login). NEXUS authenticates no person and never aggregates by "
             "person; a consumer that groups these does so on its own account."),
}


def action_receipt(lineage, chain, action_id: str, *, key_path, disclose_identity: bool = True) -> dict:
    """W10b (D7) — a governed action, provable offline: every field its
    record hash is over, the hash's leaf path to the sealing block's `action`
    root, the block's header and signature, the key list and any anchor.
    The accountable identity is in the fields as its salted commitment; with
    `disclose_identity` (the gated receipt) its value and salt ride beside,
    declared per-person; without (the open lookup by signature) they do not,
    and the receipt still proves the sealed action. Refused by name, never
    issued: an unknown id, an action whose outcome is not recorded
    (`incomplete`), one no seal covers yet, and one whose block or fields do
    not hold it."""
    from backend.memory import actions as _actions
    from backend.memory import merkle
    row = lineage.action(action_id)
    if row is None:
        raise CredentialRefused("no_action: no action has this id")
    if row["record_hash"] is None:
        raise CredentialRefused("incomplete: the action's outcome is not recorded; only a recorded outcome is "
                                "proved")
    if row["sealed_block"] is None:
        raise CredentialRefused("unsealed: the action is sealed by the next seal; ask again after it")
    fields = {k: row[k] for k in _actions.HASHED_FIELDS}
    if _actions.record_hash(fields) != row["record_hash"]:
        raise CredentialRefused("does_not_verify: the action's fields do not hash to its record hash")
    block = chain.get_block(int(row["sealed_block"]))
    seal = ((block.fingerprint or {}).get("action_seal") or {}) if block is not None else {}
    leaves, ids = seal.get("leaves") or [], seal.get("action_ids") or []
    if action_id not in ids or leaves[ids.index(action_id)] != row["record_hash"]:
        raise CredentialRefused("does_not_verify: the block that sealed the action does not hold it")
    index = ids.index(action_id)
    path = merkle.audit_path([bytes.fromhex(h) for h in leaves], index)
    key = _ik.load_or_create(key_path, chain=chain)
    keys = _ik.identity_document(key, chain)["keys"]
    signature = _signature(block, key)
    out = {"receipt": ACTION_RECEIPT, "version": VERSION, "action_id": action_id, "fields": fields,
           "record_hash": row["record_hash"], "leaf_index": index, "tree_size": len(leaves),
           "path": [p.hex() for p in path],
           "block": {"layout": block.layout, "index": block.index, "block_hash": block.block_hash,
                     "header": block.header()},
           "signatures": [signature], "keys": keys,
           "anchors": [a for a in [chain.anchor_for(block.index)] if a]}
    if disclose_identity:
        out["disclosed"] = {"accountable_identity": {"value": row["accountable_identity"],
                                                     "salt": row["identity_salt"]}}
        out["per_person"] = ACTION_PER_PERSON
    return out


CHAIN = "nexus.chain"


def chain_document(chain, *, key_path=None) -> dict:
    """W11d (docs/TALIMA_SPEC.md, section 11) — the chain as one document a stranger can check with
    `talima-verify --chain`: every block's header and hash, the leaves each of its roots is over, a signature
    for each block, the key list and any anchors. Hashes only: no record, no field and no content is in it.

    A block appended since W8 hashes its header (layout 2) and is carried; a store whose first blocks are older
    (layout 1: the hash covers the whole fingerprint, which is the store's and not a document's) is carried from
    its first layout-2 block, and the verifier says where the document begins. A block signed as it was appended
    carries that signature (`seal`). One appended unsigned is signed now, by the current key, and says so
    (`issue`), which needs `key_path`; without it the document is refused by name, and when no block needs it
    the document lists no keys: a verifier pins them from the identity document, never from here."""
    from backend.memory.chain import SEAL_ROOTS, seal_leaves
    blocks = [b for b in chain.all_blocks() if b.layout != 1]
    if not blocks:
        raise CredentialRefused("no_blocks: the chain holds no block a chain document carries")
    key = _ik.load_or_create(key_path, chain=chain) if key_path is not None else None
    signatures = []
    for block in blocks:
        if not block.instance_signature and key is None:
            raise CredentialRefused(f"unsigned: block {block.index} was appended unsigned, and no key was given to "
                                    "sign it at issue")
        signatures.append(_signature(block, key))
    listed = [{"layout": block.layout, "index": block.index, "block_hash": block.block_hash, "header": block.header(),
               "leaves": {name: [leaf.hex() for leaf in seal_leaves(block.fingerprint[seal])]
                          for seal, name in SEAL_ROOTS if isinstance((block.fingerprint or {}).get(seal), dict)}}
              for block in blocks]
    return {"chain": CHAIN, "version": VERSION, "blocks": listed, "signatures": signatures,
            "keys": _ik.identity_document(key, chain)["keys"] if key is not None else [],
            "anchors": [a for a in (chain.anchor_for(block.index) for block in blocks) if a]}


def build_credential(lineage, chain, lineage_id: str, *, key_path, disclose=()) -> dict:
    row = lineage.conn.execute("SELECT * FROM lineage_records WHERE lineage_id = ?", (lineage_id,)).fetchone()
    if row is None:
        raise CredentialRefused("no_record: no lineage record has this id")
    root = lineage.commitment_root(lineage_id)
    if not root:
        raise CredentialRefused("no_commitments: the record has no commitment root (its values could not be "
                                "committed), so nothing in it can be disclosed or proved field by field")
    body = LineageStore.hashed_body(_hash_payload_from_row(row))
    salts = lineage.salts(lineage_id)
    tombstoned = lineage.tombstoned()
    if lineage_id in tombstoned:
        if not lineage.verify_tombstoned(row):
            raise CredentialRefused("does_not_verify: the tombstoned record's kept openings or root do not verify")
        commitments = {r["field"]: r["commitment"] for r in lineage.conn.execute(
            "SELECT field, commitment FROM record_field_commitments WHERE lineage_id = ?", (lineage_id,))}
        openable = set(tombstoned[lineage_id])
    else:
        if not lineage.root_rebuilds(lineage_id):
            raise CredentialRefused("does_not_verify: the record's fields and salts do not rebuild its root")
        commitments = {f: _commitments.commitment(salts[f], body[f]).hex() for f in body}
        openable = set(body)
    disclosed = {}
    for field in disclose:
        if field not in commitments:
            raise CredentialRefused(f"unknown_field: the record commits no field named {field!r}")
        if field not in openable:
            raise CredentialRefused(f"dropped: {field!r} was disposed of by the record's tombstone")
        disclosed[field] = {"value": body[field], "salt": salts[field].hex()}
    proof = _proofs.build_proof(lineage, chain, lineage_id)
    if not proof or proof.get("state") != "sealed":
        raise CredentialRefused(f"unsealed: the record's proof is {proof and proof.get('state')!r}; it is issued "
                                "once the record is sealed")
    why = _proofs.verify_proof(proof)
    if why:
        raise CredentialRefused(f"does_not_verify: {why}")
    sealed_root = proof.get("commitment_root") or (proof.get("binding") or {}).get("commitment_root")
    if sealed_root != root:
        raise CredentialRefused("unbound: no seal covers the record's commitment root yet; the next seal binds it")
    key = _ik.load_or_create(key_path, chain=chain)
    blocks = [proof["block"]] + ([proof["binding"]["block"]] if "binding" in proof else [])
    keys = _ik.identity_document(key, chain)["keys"]
    signatures = [_signature(chain.get_block(int(ref["index"])), key) for ref in blocks]
    scope = row["record_scope"] if "record_scope" in row.keys() else None
    if "lineage_id" not in disclosed:
        # The record hash rides the proof's leaf (W8), and it is over every field, the lineage id among them: a
        # credential that named the id in clear would let a guess of an undisclosed field be tested against it.
        # Undisclosed, the id's random bits hide every undisclosed field together (the verifier's MF4).
        proof = {k: v for k, v in proof.items() if k != "lineage_id"}
        if "binding" in proof:
            proof["binding"] = {k: v for k, v in proof["binding"].items() if k != "lineage_id"}
    return {
        "credential": CREDENTIAL, "version": VERSION,
        "commitment_root": root, "commitments": dict(sorted(commitments.items())),
        "disclosed": disclosed, "proof": proof, "signatures": signatures,
        "keys": keys,
        "scope": declaration(scope),
        "decision_fields": [f for f in DECISION_FIELDS if f in commitments],
        # W9 (D7) — each block's RFC 3161 receipt, when an authority stamped it.
        "anchors": [a for a in (chain.anchor_for(int(ref["index"])) for ref in blocks) if a],
    }
