#!/usr/bin/env python3
"""A minimal Talima v1 producer, written from docs/TALIMA_SPEC.md alone.

    python minimal_producer.py OUT_FOLDER [--seed HEX]

One file, the Python standard library only, and nothing of NEXUS: it exists to show that the specification is
enough to build from. It makes a key, three records, three linked blocks, and writes into OUT_FOLDER:

    identity.json             the producer's keys (section 8)
    credential.json           record 1 with one field disclosed (section 9)
    credential_full.json      record 0 with every field disclosed
    commitment_receipt.json   a held commitment (section 10), with commitment_content.txt and commitment_salt.txt
    action_receipt.json       a recorded action (section 10)
    chain.json                the three blocks, their leaves and signatures (section 11)

`talima-verify` accepts each of them. A rule that code carries is one function or constant below, named beside
it, so a test can replace one and see the verifier refuse what it then makes. Not every rule is carried by a
line: M-CAN-6 to M-CAN-8 say what a producer must not write (nothing here writes a name twice, half of a
character pair or upper-case hexadecimal), M-DOC-4 follows from the others (a disclosure opens because
`commitment` made it), and M-ANC-1 is about anchors, which this producer does not carry. The helpers that carry
no rule are the Ed25519 arithmetic and the tree's splitting. With --seed the output is the same bytes on
every run; without, the key and the salts are fresh. It is an example to read, not a library: it keeps its key
in memory and stores nothing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

GENESIS = "0" * 64                                   # M-CHN-3
SALT_BYTES = 32                                      # M-COM-2
STATEMENT_PREFIX = b"NEXUS-BLOCK-V1\x00"             # M-SIG-1
CREDENTIAL, COMMITMENT_RECEIPT, ACTION_RECEIPT, CHAIN = "nexus.credential", "nexus.commitment", "nexus.action", \
    "nexus.chain"                                    # M-DOC-1, M-RCP-1, M-ACT-1, M-CHD-1
VERSION = 1
ACTION_FIELDS = ("action_id", "kind", "evidence_class", "method", "target", "request_hash", "credentials_json",
                 "lineage_id", "trigger_route", "identity_commitment", "identity_mechanism", "signature_key_id",
                 "signature", "requested_at", "state", "status", "response_hash", "completed_at")     # M-ACT-2


# ---------------------------------------------------------------- section 3: the canonical encoding

def canonical(value) -> bytes:
    """M-CAN-1 to M-CAN-5. Python's own encoder writes exactly the form section 3 describes."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def H(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


# ---------------------------------------------------------------- section 6: Merkle trees

def leaf_hash(leaf: bytes) -> bytes:
    return H(b"\x00" + leaf)                         # M-MRK-1


def node_hash(left: bytes, right: bytes) -> bytes:
    return H(b"\x01" + left + right)                 # M-MRK-1


def _split(n: int) -> int:
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def _subtree(hashes: list) -> bytes:
    if len(hashes) == 1:
        return hashes[0]
    k = _split(len(hashes))
    return node_hash(_subtree(hashes[:k]), _subtree(hashes[k:]))


def merkle_root(leaves: list) -> bytes:
    return _subtree([leaf_hash(x) for x in leaves]) if leaves else H(b"")     # M-MRK-1


def audit_path(leaves: list, index: int) -> list:
    """M-MRK-2: RFC 6962, 2.1.1, from the leaf's sibling up."""
    def path(hashes: list, m: int) -> list:
        if len(hashes) == 1:
            return []
        k = _split(len(hashes))
        if m < k:
            return path(hashes[:k], m) + [_subtree(hashes[k:])]
        return path(hashes[k:], m - k) + [_subtree(hashes[:k])]
    return path([leaf_hash(x) for x in leaves], index)


# ---------------------------------------------------------------- section 8: Ed25519 (RFC 8032, 5.1), signing

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_GY = 4 * pow(5, _P - 2, _P) % _P


def _recover_x(y: int) -> int:
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * pow(2, (_P - 1) // 4, _P) % _P
    return _P - x if x & 1 else x


_G = (_recover_x(_GY), _GY, 1, _recover_x(_GY) * _GY % _P)


def _add(a, b):
    A, B = (a[1] - a[0]) * (b[1] - b[0]) % _P, (a[1] + a[0]) * (b[1] + b[0]) % _P
    C, D = 2 * a[3] * b[3] * _D % _P, 2 * a[2] * b[2] % _P
    E, F, G, Hh = B - A, D - C, D + C, B + A
    return (E * F % _P, G * Hh % _P, F * G % _P, E * Hh % _P)


def _mul(s: int, point):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, point)
        point = _add(point, point)
        s >>= 1
    return q


def _compress(point) -> bytes:
    zi = pow(point[2], _P - 2, _P)
    x, y = point[0] * zi % _P, point[1] * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _expand(seed: bytes):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    return (a & ((1 << 254) - 8)) | (1 << 254), h[32:]


def public_key(seed: bytes) -> bytes:
    return _compress(_mul(_expand(seed)[0], _G))


def sign(seed: bytes, message: bytes) -> bytes:
    a, prefix = _expand(seed)
    A = _compress(_mul(a, _G))
    r = int.from_bytes(hashlib.sha512(prefix + message).digest(), "little") % _L
    R = _compress(_mul(r, _G))
    h = int.from_bytes(hashlib.sha512(R + A + message).digest(), "little") % _L
    return R + ((r + h * a) % _L).to_bytes(32, "little")


def key_id(public: bytes) -> str:
    return H(public).hex()                           # M-KEY-1


def statement(index: int, block_hash: str) -> bytes:
    return STATEMENT_PREFIX + index.to_bytes(8, "big") + bytes.fromhex(block_hash)     # M-SIG-1


# ---------------------------------------------------------------- sections 4 and 5: the record and its commitments

def record_hash(body: dict) -> bytes:
    return H(canonical(body))                        # M-REC-3


def commitment(salt: bytes, value) -> bytes:
    return H(salt + canonical(value))                # M-COM-1


def commitment_leaf(name: str, c: bytes) -> bytes:
    return name.encode("utf-8") + b"\x00" + c        # M-COM-4


def commitment_root(commitments: dict) -> bytes:
    return merkle_root([commitment_leaf(name, commitments[name]) for name in sorted(commitments)])     # M-COM-4


def lineage_leaf(rec: dict) -> bytes:
    return rec["hash"] + rec["root"]                 # M-BLK-4


# ---------------------------------------------------------------- section 10: what a receipt is of

def held_commitment(salt: bytes, content: str) -> bytes:
    return commitment(salt, content)                 # M-RCP-2: the content is a JSON string


def commit_leaf(held: dict) -> bytes:
    return held["commitment"]                        # M-RCP-3: the leaf is the commitment itself


def action_hash(fields: dict) -> bytes:
    return H(canonical(fields))                      # M-ACT-3


def action_leaf(action: dict) -> bytes:
    return action["hash"]                            # M-ACT-4: the leaf is the action's record hash


# ---------------------------------------------------------------- section 7: blocks

def root_entry(leaves: list) -> dict:
    return {"root": merkle_root(leaves).hex(), "size": len(leaves)}     # M-BLK-5


def header_of(index: int, timestamp, fingerprint: dict, roots: dict, previous_hash: str) -> dict:
    """M-BLK-2, M-BLK-3: this producer snapshots no file, and its blocks are governance blocks."""
    return {"layout": 2, "index": index, "timestamp": timestamp,
            "fingerprint_hash": H(canonical(fingerprint)).hex(), "roots": roots,
            "snapshot_path": "", "snapshot_hash": H(b"").hex(), "previous_hash": previous_hash,
            "snapshot_missing": True, "kind": "governance"}


def block_hash(header: dict) -> str:
    return H(canonical(header)).hex()                # M-BLK-1


# ---------------------------------------------------------------- the producer

class Producer:
    """A key, and a chain it appends to. `salt(label)` gives the 32 bytes for one use."""

    def __init__(self, seed: "bytes | None" = None):
        self.seed = seed
        self.count = 0
        self.key = self.salt("the signing key") if seed is not None else os.urandom(32)
        self.public = public_key(self.key)
        self.blocks: list = []

    def salt(self, label: str) -> bytes:
        """M-COM-2. 32 random bytes; from the seed, when one was given, so a run can be repeated."""
        self.count += 1
        if self.seed is None:
            return os.urandom(SALT_BYTES)
        return H(self.seed + f"{self.count}:{label}".encode("utf-8"))[:SALT_BYTES]

    def record(self, body: dict) -> dict:
        salts = {name: self.salt(f"field {name}") for name in body}                      # M-COM-3: one per field
        commitments = {name: commitment(salts[name], body[name]) for name in body}
        return {"body": body, "salts": salts, "commitments": commitments, "root": commitment_root(commitments),
                "hash": record_hash(body)}

    def next_index(self) -> int:
        return len(self.blocks)                      # M-CHN-1: 0, 1, 2, ...

    def previous_hash(self) -> str:
        return self.blocks[-1]["block_hash"] if self.blocks else GENESIS     # M-CHN-2, M-CHN-3

    def append(self, timestamp, leaves: dict) -> dict:
        """One block over `leaves` ({root name: [leaf bytes]}), linked to the last and signed."""
        index, previous = self.next_index(), self.previous_hash()
        fingerprint = {name: [leaf.hex() for leaf in ls] for name, ls in leaves.items()}
        if index == 0:                               # section 8: block 0 announces the key, in its fingerprint
            fingerprint["key"] = {"key_id": key_id(self.public), "public_key": self.public.hex()}
        header = header_of(index, timestamp, fingerprint, {name: root_entry(ls) for name, ls in leaves.items()},
                           previous)
        digest = block_hash(header)
        block = {"layout": 2, "index": index, "block_hash": digest, "header": header, "leaves": leaves,
                 "signature": {"block_index": index, "block_hash": digest, "key_id": key_id(self.public),
                               "value": sign(self.key, statement(index, digest)).hex(), "signed": "seal"}}
        self.blocks.append(block)
        return block

    def keys(self) -> list:
        first = self.blocks[0]["header"]["timestamp"] if self.blocks else None
        return [{"key_id": key_id(self.public), "public_key": self.public.hex(), "algorithm": "ed25519",
                 "reason": "first", "previous_key_id": None, "announced_in": 0, "until_block": None,
                 "active_from": first, "active_to": None}]                               # M-KEY-2

    def identity(self, created_at) -> dict:
        return {"algorithm": "ed25519", "public_key": self.public.hex(), "key_id": key_id(self.public),
                "created_at": created_at, "keys": self.keys()}


def _ref(block: dict) -> dict:
    return {k: block[k] for k in ("layout", "index", "block_hash", "header")}


def _place(block: dict, root: str, leaf: bytes) -> dict:
    """Where `leaf` is under the block's root: its index, the tree's size and its path (M-MRK-2)."""
    leaves = block["leaves"][root]
    index = leaves.index(leaf)
    return {"leaf_index": index, "tree_size": len(leaves), "path": [p.hex() for p in audit_path(leaves, index)]}


def credential(producer: Producer, rec: dict, block: dict, disclose) -> dict:
    leaf = lineage_leaf(rec)
    return {"credential": CREDENTIAL, "version": VERSION,
            "commitment_root": rec["root"].hex(),
            "commitments": {name: rec["commitments"][name].hex() for name in sorted(rec["commitments"])},
            "disclosed": {name: {"value": rec["body"][name], "salt": rec["salts"][name].hex()} for name in disclose},
            "proof": {"proof_version": 1, "state": "sealed", "seal": "lineage", "record_hash": rec["hash"].hex(),
                      "commitment_root": rec["root"].hex(), "leaf": leaf.hex(), **_place(block, "lineage", leaf),
                      "block": _ref(block),                                              # M-DOC-2, M-DOC-3
                      **({"lineage_id": rec["body"]["lineage_id"]} if "lineage_id" in disclose else {})},  # M-DOC-6
            "signatures": [block["signature"]], "keys": producer.keys(),                 # M-SIG-2, M-SIG-3
            "scope": {"record_scope": rec["body"].get("record_scope", "full_turn")},
            "decision_fields": [], "anchors": []}                                        # M-DOC-5


def commitment_receipt(producer: Producer, held: dict, block: dict) -> dict:
    return {"receipt": COMMITMENT_RECEIPT, "version": VERSION, "commitment_id": held["id"],
            "commitment": held["commitment"].hex(), "held_at": block["header"]["timestamp"],
            **_place(block, "commit", commit_leaf(held)), "block": _ref(block),          # M-RCP-3
            "signatures": [block["signature"]], "keys": producer.keys(), "anchors": []}


def action_receipt(producer: Producer, action: dict, block: dict) -> dict:
    return {"receipt": ACTION_RECEIPT, "version": VERSION, "action_id": action["fields"]["action_id"],
            "fields": action["fields"], "record_hash": action["hash"].hex(),
            **_place(block, "action", action_leaf(action)), "block": _ref(block),        # M-ACT-4
            "signatures": [block["signature"]], "keys": producer.keys(), "anchors": [],
            "disclosed": {"accountable_identity": {"value": action["person"], "salt": action["salt"].hex()}}}


def chain_document(producer: Producer) -> dict:
    return {"chain": CHAIN, "version": VERSION,
            "blocks": [{**_ref(b), "leaves": {name: [leaf.hex() for leaf in ls] for name, ls in b["leaves"].items()}}
                       for b in producer.blocks],                                        # M-CHD-2
            "signatures": [b["signature"] for b in producer.blocks], "keys": producer.keys(), "anchors": []}


def an_action(producer: Producer, *, when) -> dict:
    """An action as section 10 describes it: a request this producer sent, recorded with its outcome."""
    person, salt = "operator@example.org", producer.salt("the accountable identity")
    fields = {"action_id": producer.salt("action id").hex()[:32], "kind": "web_search", "evidence_class": "runtime",
              "method": "GET", "target": "https://search.example.org/search",
              "request_hash": H(b"GET https://search.example.org/search?q=talima").hex(),
              "credentials_json": "[]", "lineage_id": None, "trigger_route": "/search/execute",
              "identity_commitment": commitment(salt, person).hex(), "identity_mechanism": None,     # M-ACT-5
              "signature_key_id": None, "signature": None, "requested_at": when, "state": "completed",
              "status": "200", "response_hash": H(b"the response's bytes").hex(), "completed_at": when + 1}
    assert set(fields) == set(ACTION_FIELDS)                                             # M-ACT-2
    return {"fields": fields, "hash": action_hash(fields), "person": person, "salt": salt}


def produce(seed: "bytes | None" = None, *, started=1700000000) -> dict:
    """Every document, by file name. The times are plain numbers; one is not an integer, on purpose (M-CAN-4)."""
    producer = Producer(seed)
    bodies = [{"lineage_id": producer.salt(f"lineage id {n}").hex()[:32], "ts": started + n,       # M-REC-1, M-REC-2
               "query_hash": H(f"question {n}".encode()).hex(), "injected": [], "contradictions": [],
               "model_id": "exemple-modèle", "prompt_prefix_hash": H(b"You answer from the record.").hex(),
               "response_hash": H(f"answer {n}".encode()).hex()} for n in (0, 1, 2)]      # M-REC-4: no default stated
    records = [producer.record(body) for body in bodies]
    content, content_salt = "The forecast for Friday, written on Monday.", producer.salt("the held content")
    held = {"id": producer.salt("commitment id").hex()[:32], "commitment": held_commitment(content_salt, content)}
    action = an_action(producer, when=started + 20)
    first = producer.append(started + 10.5, {"lineage": [lineage_leaf(r) for r in records]})
    second = producer.append(started + 30, {"commit": [commit_leaf(held)], "action": [action_leaf(action)]})
    producer.append(started + 40, {})
    return {"identity.json": producer.identity(started),
            "credential.json": credential(producer, records[1], first, ["model_id"]),
            "credential_full.json": credential(producer, records[0], first, sorted(records[0]["body"])),
            "commitment_receipt.json": commitment_receipt(producer, held, second),
            "commitment_content.txt": content, "commitment_salt.txt": content_salt.hex(),
            "action_receipt.json": action_receipt(producer, action, second),
            "chain.json": chain_document(producer)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A minimal Talima v1 producer, written from docs/TALIMA_SPEC.md alone.")
    ap.add_argument("out", help="the folder to write the documents into")
    ap.add_argument("--seed", default=None, help="hexadecimal; the same seed writes the same documents")
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, document in produce(bytes.fromhex(args.seed) if args.seed else None).items():
        text = document if isinstance(document, str) else json.dumps(document, indent=1, sort_keys=True) + "\n"
        with open(out / name, "w", encoding="utf-8", newline="") as f:
            f.write(text)
    print(f"written to {out}: " + ", ".join(sorted(p.name for p in out.iterdir())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
