#!/usr/bin/env python3
"""nexus-verify — check a NEXUS decision credential offline (W9, run/specs/W9.md D6).

    python nexus_verify.py CREDENTIAL.json [--identity IDENTITY.json | --key PUBLIC_KEY_HEX] [--json]
    python nexus_verify.py --reveal RECEIPT.json --content NOTE.txt --salt SALT_HEX [--identity ...]
    python nexus_verify.py --action ACTION.json [--identity ...]   (a governed action's receipt, W10b)
    python nexus_verify.py --chain CHAIN.json [--identity ...]     (a chain document, W11d)
    any of them with --tsa-cert AUTHORITY.pem to check the anchor receipts it carries

One file, the Python standard library only, and no NEXUS import: it runs with
nothing but the credential, the instance's published identity (the body of
its `GET /identity`) and Python 3.9 or later. Exit 0 when the credential
verifies, 1 when it does not (each failure named), 2 when the input cannot be
read.

WHAT A VERIFIED CREDENTIAL PROVES. That the holder of the key named sealed a
record whose disclosed fields hold exactly the values shown, in a block whose
hash that key signed; that every other field of the record is committed to,
under a salt, and appears here only as that commitment; and, for a record
sealed before its commitments existed, that the instance bound those
commitments to the sealed record later (the binding). WHAT IT DOES NOT PROVE.
That the content is true. When the record was sealed: a block's time is the
instance's own word until an anchor receipt verifies (anchoring, D7). Who held
the key: pin the instance's key from a source you trust (`--identity` or
`--key`); without one, the credential's own list is used and the check fails
`key_unpinned`.

Checked, and the name each failure carries:
  disclosure_mismatch   a disclosed value and salt do not open the field's commitment
  unsalted_hash         a disclosed field whose salt is not 32 bytes, or whose commitment
                        is the bare hash of its value (no salt at all)
  commitments_mismatch  the field commitments do not make the commitment root
  root_not_sealed       the root is not the one the record's seal (or binding) holds
  (a proof refusal)     W8's: leaf_mismatch, block_hash_mismatch, size_mismatch,
                        not_in_list, index_out_of_range, path_too_long,
                        path_too_short, root_mismatch, binding_* and malformed
  unsigned              no signature covers the block the record (or its binding) is in
  signature_invalid     a signature does not verify, or names a key not on the list
  reveal_mismatch       (--reveal) the content and salt do not open the held commitment
  action_mismatch       (--action) the action's fields do not hash to its record hash
  anchor_invalid        an anchor receipt that does not date this block: not granted, another
                        imprint, a message digest or signature that does not hold, or a
                        certificate that is not the authority's (--tsa-cert)
  signature_outside_key_period   a signature over a block outside every range of blocks
                        the key was active for (a period is blocks, from the one that
                        announced the key to the one where the next key took over; a
                        signature made at issue is held to the period's end only)
  key_unpinned          no --identity or --key was given
  malformed             input that is not a credential, a receipt or a chain document of this version, or
                        that section 3 of docs/TALIMA_SPEC.md refuses: as it is read, a NaN or a number
                        past every float, a name given twice in one object, half of a character pair;
                        where it is decoded, a byte string not in lower-case hexadecimal; and a member of
                        the document's own structure typed as an integer that is not one
  record_malformed      (W11d, docs/TALIMA_SPEC.md section 4) the record commits no field of a core name, a
                        disclosed core field is of another type, or a disclosed field states its default
  record_mismatch       every field is disclosed, and together they do not hash to the record's hash
  block_malformed       a block's header lacks a member, has one of another type, states a default, or
                        seals a tree of no leaves under another root than the empty one; or the block is
                        labelled a layout its document does not rest on (a proof: 2, or the older 1; a
                        binding, a receipt and a chain: 2)
  key_id_mismatch       a signature's key id is not the SHA-256 of the public key listed under it
  index_gap             (--chain) the blocks' indices do not run in order with none missing
  chain_link_broken     (--chain) a block's previous hash is not the hash of the block before it, block
                        0's is not 64 zeros, or a later block's is
  roots_mismatch        (--chain) the leaves listed for a root are not the leaves that root is over
  leaf_malformed        (--chain) a listed leaf is not as long as its root's leaves are
  lineage_id_mismatch   the proof names a record, and the credential does not disclose that name

Said in a note, which changes no verdict:
  signed_at_issue       a block's signature was made when the document was issued, not as the block was
                        appended
  record_recomputed     every field is disclosed, and together they hash to the record's hash
  accountable           (--action) the accountable identity: disclosed, or committed and not shown
  action                (--action) what the action was, and what kind of evidence its record is
  anchored              an anchor receipt dates a block
  anchor_unpinned       an anchor receipt was read with no certificate to check its signature (--tsa-cert)
  no_anchor             --tsa-cert was given and a block carries no anchor
  partial_chain         (--chain) the document begins after block 0; nothing before it is checked
  leaves_not_listed     (--chain) some roots list no leaves here, and are taken as sealed
  bare_hash_leaves      (--chain) a block sealed a record as its bare hash, the older form
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys

VERSION = 1
SALT_BYTES = 32
STATEMENT_PREFIX = b"NEXUS-BLOCK-V1\x00"

# ---------- RFC 8032 (Ed25519), verification only, after the RFC's own Python ----------

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)


def _recover_x(y: int, sign: int):
    if y >= _P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * pow(5, _P - 2, _P) % _P
_GX = _recover_x(_GY, 0)
_G = (_GX, _GY, 1, _GX * _GY % _P)


def _add(a, b):
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _mul(s: int, point):
    q = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            q = _add(q, point)
        point = _add(point, point)
        s >>= 1
    return q


def _equal(a, b) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _decompress(s: bytes):
    if len(s) != 32:
        return None
    y = int.from_bytes(s, "little")
    sign, y = y >> 255, y & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """True when `signature` is the Ed25519 signature of `message` under
    `public_key` (RFC 8032 §5.1.7, the cofactorless check)."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a, r = _decompress(public_key), _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little") % _L
    return _equal(_mul(s, _G), _add(r, _mul(h, a)))


# ---------- RFC 6962 / RFC 9162 Merkle trees ----------

def _leaf_hash(leaf: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + leaf).digest()


def _node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def merkle_root(leaves: list) -> bytes:
    if not leaves:
        return hashlib.sha256(b"").digest()

    def sub(hashes):
        if len(hashes) == 1:
            return hashes[0]
        k = 1
        while k * 2 < len(hashes):
            k *= 2
        return _node_hash(sub(hashes[:k]), sub(hashes[k:]))
    return sub([_leaf_hash(x) for x in leaves])


def verify_inclusion(leaf: bytes, index: int, size: int, path: list, expected_root: bytes):
    """None when `path` proves `leaf` at `index` in the tree of `size` whose
    root is `expected_root`; otherwise why not. RFC 9162 §2.1.3.2."""
    if not isinstance(index, int) or not isinstance(size, int) or isinstance(index, bool) or isinstance(size, bool):
        return "malformed: the index and the size are integers"
    if any(not isinstance(p, (bytes, bytearray)) or len(p) != 32 for p in path) \
            or not isinstance(expected_root, (bytes, bytearray)) or len(expected_root) != 32:
        return "malformed: every path element and the root are 32-byte hashes"
    if index < 0 or index >= size:
        return f"index_out_of_range: leaf {index} in a tree of {size}"
    fn, sn, r = index, size - 1, _leaf_hash(leaf)
    for p in path:
        if sn == 0:
            return "path_too_long: the path has more elements than the tree has levels"
        if fn & 1 or fn == sn:
            r = _node_hash(p, r)
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            r = _node_hash(r, p)
        fn >>= 1
        sn >>= 1
    if sn != 0:
        return "path_too_short: the path ends below the root"
    if r != expected_root:
        return "root_mismatch: the path leads to another root"
    return None


# ---------- W8's commitments and blocks ----------

def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def commitment(salt: bytes, value) -> bytes:
    return hashlib.sha256(salt + canonical(value)).digest()


def commitment_root(commitments: dict) -> bytes:
    return merkle_root([name.encode("utf-8") + b"\x00" + commitments[name] for name in sorted(commitments)])


def _is_hash(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _canonical_hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _hex(text) -> bytes:
    """Bytes a document writes as text: lower-case hexadecimal, and nothing else (M-CAN-8)."""
    if not isinstance(text, str) or any(c not in "0123456789abcdef" for c in text):
        raise ValueError("a byte string is written in lower-case hexadecimal")
    return bytes.fromhex(text)


def _layout_1_hash(body: dict) -> str:
    hashed = {k: body[k] for k in ("index", "timestamp", "fingerprint", "snapshot_path", "snapshot_hash",
                                   "previous_hash")}
    if body.get("snapshot_missing"):
        hashed["snapshot_missing"] = True
    if body.get("kind", "snapshot") != "snapshot":
        hashed["kind"] = body["kind"]
    return _canonical_hash(hashed)


def _verify_binding(binding: dict, record_hash: str):
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
    why = verify_inclusion(bytes.fromhex(binding["leaf"]), int(binding["leaf_index"]), int(binding["tree_size"]),
                           [bytes.fromhex(x) for x in binding["path"]], bytes.fromhex(entry["root"]))
    return None if why is None else f"binding_{why}"


def verify_proof(proof: dict):
    """None when a W8 proof holds; otherwise why not."""
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
            root = merkle_root([bytes.fromhex(h) for h in hashes])
        why = verify_inclusion(leaf, int(proof["leaf_index"]), int(proof["tree_size"]), path, root)
        if why is None and "binding" in proof:
            why = _verify_binding(proof["binding"], proof["record_hash"])
        return why
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError) as e:
        return f"malformed: {type(e).__name__}"


# ---------- W11d: the rules docs/TALIMA_SPEC.md states that the checks above did not hold ----------
# Kept out of `verify_proof`, which stays W8's own check, verdict for verdict (tests/test_w9_nexus_verify.py V5).

# Section 4: a record's core fields with their types, and the defaults a body says by absence.
CORE_FIELDS = {"lineage_id": "string", "ts": "number", "query_hash": "hash", "injected": "array",
               "contradictions": "array", "model_id": "string", "prompt_prefix_hash": "hash",
               "response_hash": "hash"}
BODY_DEFAULTS = {"scope": "default", "record_scope": "full_turn", "learning_mode": "live",
                 "identity_mechanism": "asserted"}
HEADER_MEMBERS = (("layout", "integer"), ("index", "integer"), ("timestamp", "number"),
                  ("fingerprint_hash", "hash"), ("snapshot_path", "string"), ("snapshot_hash", "hash"),
                  ("previous_hash", "hash"))
GENESIS_HASH = "0" * 64
EMPTY_ROOT = hashlib.sha256(b"").hexdigest()        # M-MRK-1: the root of no leaves
# Section 7: what a leaf under each root is, in bytes. A root this table does not name has leaves of any length.
# A `lineage` leaf of 32 bytes is the older form (Appendix A): a record sealed as its bare hash and bound later.
LEAF_BYTES = {"lineage": (64, 32), "binding": (64,), "commit": (32,), "action": (32,)}
CHAIN = "nexus.chain"


def _is(kind: str, value) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "hash":
        return _is_hash(value)
    return kind == "array" and isinstance(value, list)


# The members of a document's OWN structure that the specification types as integers. A JSON `true` or `1.0` is
# not one (M-CAN-4: the two number forms are different values), though Python calls them equal. They are named by
# where they stand, because a name alone says nothing: `commitments` and `disclosed` are keyed by the record's
# field names, and a record may call a field `index`; a key entry, an action's fields and a producer's own
# statements may hold members of any name. A header is typed by `_header_problem`, which names its block.
PERIOD = ("announced_in", "from_block", "until_block")       # a key's period, in blocks; null where it is open


def _not_integers(where: str, holder, names: tuple, null_too: bool = False) -> "str | None":
    if not isinstance(holder, dict):
        return None                                  # its absence or its shape is another check's to name
    for name in names:
        if name in holder and not (_is("integer", holder[name]) or (null_too and holder[name] is None)):
            return f"{name} in {where} is not an integer"
    return None


def _own_structure_problem(document, kind: str) -> "str | None":
    """The first integer-typed member of the document's own structure that is not an integer; None when all are.
    `kind` is what the document is (`credential`, `receipt` or `chain`), because each types its own members and
    no other's: a producer's own member in a credential may be called `blocks`, and in a chain `proof`."""
    if not isinstance(document, dict):
        return "the document is not an object"
    checks = [("the document", document, ("version",), False)]
    if kind == "credential":
        proof = document.get("proof")
        if isinstance(proof, dict):
            for where, place in (("the proof", proof), ("the proof's binding", proof.get("binding"))):
                if isinstance(place, dict):
                    checks.append((where, place, ("leaf_index", "tree_size"), False))
                    checks.append((f"{where}'s block", place.get("block"), ("layout", "index"), False))
            block = proof.get("block")
            if isinstance(block, dict) and block.get("layout") == 1:     # the older layout carries the block's body
                checks.append(("the proof's block's body", block.get("body"), ("index",), False))
    elif kind == "receipt":
        checks.append(("the document", document, ("leaf_index", "tree_size"), False))
        checks.append(("the document's block", document.get("block"), ("layout", "index"), False))
    else:
        blocks = document.get("blocks")
        for n, block in enumerate(blocks if isinstance(blocks, list) else []):
            checks.append((f"block {n} of the document", block, ("layout", "index"), False))
    for member, names, null_too in (("signatures", ("block_index",), False), ("anchors", ("block_index",), False),
                                    ("keys", PERIOD, True)):
        items = document.get(member)
        for n, item in enumerate(items if isinstance(items, list) else []):
            checks.append((f"{member}[{n}]", item, names, null_too))
    for where, holder, names, null_too in checks:
        why = _not_integers(where, holder, names, null_too)
        if why:
            return why
    return None


def _strict(document, kind: str) -> None:
    """Raise, as a malformed document, on an integer-typed member of its own structure that is not an integer."""
    why = _own_structure_problem(document, kind)
    if why:
        raise ValueError(why)


def _sealed_list(block) -> list:
    """The record hashes a layout-1 block seals: a list, or the document is malformed (W8's own check would read
    an object's names as one). None where the block is not layout 1 or holds no such member, which W8's check
    names."""
    try:
        if block.get("layout") != 1:
            return []
        hashes = block["body"]["fingerprint"]["lineage_seal"]["record_hashes"]
    except (KeyError, TypeError, AttributeError):
        return []
    if not isinstance(hashes, list):
        raise ValueError("a layout-1 block's sealed list is not a list")
    return hashes


def _keys_problem(keys) -> "str | None":
    """The same for the key list the signatures are checked against, the pinned identity's included."""
    if not isinstance(keys, list):
        return "the key list is not a list"
    for n, key in enumerate(keys):
        why = _not_integers(f"key {n} of the list", key, PERIOD, True) if isinstance(key, dict) \
            else f"key {n} of the list is not an object"
        if why:
            return why
    return None


def _header_problem(header) -> "str | None":
    """Why a layout-2 header is not one (M-BLK-2, M-BLK-3); None when it is."""
    if not isinstance(header, dict):
        return "the header is not an object"
    for name, kind in HEADER_MEMBERS:
        if name not in header:
            return f"the header has no {name}"
        if not _is(kind, header[name]):
            return f"the header's {name} is not {'an' if kind[0] in 'ai' else 'a'} {kind}"
    if header["layout"] != 2 or header["index"] < 0:
        return "the header's layout is not 2, or its index is negative"
    roots = header.get("roots")
    if not isinstance(roots, dict):
        return "the header has no roots"
    for name, entry in roots.items():
        if not isinstance(entry, dict) or not _is_hash(entry.get("root")) or not _is("integer", entry.get("size")) \
                or entry["size"] < 0:
            return f"the header's root {name!r} is not a hash and a size"
        if entry["size"] == 0 and entry["root"] != EMPTY_ROOT:
            return f"the header's root {name!r} is over no leaves and is not the empty root"
    if "snapshot_missing" in header and header["snapshot_missing"] is not True:
        return "the header states snapshot_missing and it is not true: a default is said by absence"
    if "kind" in header and (not isinstance(header["kind"], str) or header["kind"] in ("snapshot", "")):
        return "the header states its kind and it is the default or empty: a default is said by absence"
    return None


def _check_headers(blocks: list, failures: list) -> None:
    for b in blocks:
        if b.get("layout") != 1:
            why = _header_problem(b.get("header"))
            if why:
                failures.append(f"block_malformed: block {b.get('index')}: {why}")


def _check_record(commitments: dict, disclosed: dict, opened: set, record_hash, failures: list, notes: list) -> None:
    """Section 4's rules, as far as this credential shows the record: the core fields are committed (their names
    always show); a disclosed core field has its type and a disclosed field states no default; and when EVERY
    field is disclosed the body they make hashes to the record's hash. With fewer the hash stays opaque, by
    design: the body holds a random identifier, so no guess at an undisclosed field can be tested against it."""
    missing = [name for name in CORE_FIELDS if name not in commitments]
    if missing:
        failures.append(f"record_malformed: the record commits no field named {', '.join(missing)}")
    for name in sorted(opened):
        value = disclosed[name]["value"]
        if name in CORE_FIELDS and not _is(CORE_FIELDS[name], value):
            failures.append(f"record_malformed: {name} is not {'an' if CORE_FIELDS[name][0] == 'a' else 'a'} "
                            f"{CORE_FIELDS[name]}")
        if name in BODY_DEFAULTS and (value is None or value == "" or value == BODY_DEFAULTS[name]):
            failures.append(f"record_malformed: {name} is stated at its default; a default is said by absence")
    if commitments and opened == set(commitments):
        body = {name: disclosed[name]["value"] for name in commitments}
        if hashlib.sha256(canonical(body)).hexdigest() != record_hash:
            failures.append("record_mismatch: every field is disclosed, and together they do not hash to the "
                            "record's hash")
        else:
            notes.append("record_recomputed: every field is disclosed, and together they hash to the record's hash")


# ---------- RFC 3161 anchors (W9, D7): DER, RSA PKCS#1 v1.5, the authority's certificate ----------

_DIGESTS = {"2.16.840.1.101.3.4.2.1": "sha256", "2.16.840.1.101.3.4.2.2": "sha384", "2.16.840.1.101.3.4.2.3": "sha512"}
_RSA_SIGNATURES = {"1.2.840.113549.1.1.1", "1.2.840.113549.1.1.11", "1.2.840.113549.1.1.12", "1.2.840.113549.1.1.13"}
_ECDSA_SIGNATURES = {"1.2.840.10045.4.3.2": "sha256", "1.2.840.10045.4.3.3": "sha384", "1.2.840.10045.4.3.4": "sha512"}
_RSA_HASHES = {"1.2.840.113549.1.1.11": "sha256", "1.2.840.113549.1.1.12": "sha384", "1.2.840.113549.1.1.13": "sha512"}
# NIST curves (FIPS 186-4 D.1.2; SEC 2): p, a, b, Gx, Gy, n. FreeTSA's stamping key is P-384.
_CURVES = {
    "1.2.840.10045.3.1.7": (
        0xffffffff00000001000000000000000000000000ffffffffffffffffffffffff,
        0xffffffff00000001000000000000000000000000fffffffffffffffffffffffc,
        0x5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b,
        0x6b17d1f2e12c4247f8bce6e563a440f277037d812deb33a0f4a13945d898c296,
        0x4fe342e2fe1a7f9b8ee7eb4a7c0f9e162bce33576b315ececbb6406837bf51f5,
        0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551),
    "1.3.132.0.34": (
        0xfffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffeffffffff0000000000000000ffffffff,
        0xfffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffeffffffff0000000000000000fffffffc,
        0xb3312fa7e23ee7e4988e056be3f82d19181d9c6efe8141120314088f5013875ac656398d8a2ed19d2a85c8edd3ec2aef,
        0xaa87ca22be8b05378eb1c71ef320ad746e1d3b628ba79b9859f741e082542a385502f25dbf55296c3a545e3872760ab7,
        0x3617de4a96262c6f5d9e98bf9292dc29f8f41dbd289a147ce9da3113b5f0b8c00a60b1ce1d7e819d7a431d7c90ea0e5f,
        0xffffffffffffffffffffffffffffffffffffffffffffffffc7634d81f4372ddf581a0db248b0a77aecec196accc52973),
    "1.3.132.0.35": (
        2 ** 521 - 1, 2 ** 521 - 4,
        0x0051953eb9618e1c9a1f929a21a0b68540eea2da725b99b315f3b8b489918ef109e156193951ec7e937b1652c0bd3bb1bf073573df883d2c34f1ef451fd46b503f00,
        0x00c6858e06b70404e9cd9e3ecb662395b4429c648139053fb521f828af606b4d3dbaa14b5e77efe75928fe1dc127a2ffa8de3348b3c1856a429bf97e7e31c2e5bd66,
        0x011839296a789a3bc0045c8a5fb42c7d1bd998f54449579b446817afbd17273e662c97ee72995ef42640c550b9013fad0761353c7086a272c24088be94769fd16650,
        0x01fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffa51868783bf2f966b7fcc0148f709a5d03bb5c9b8899c47aebb6fb71e91386409),
}


def _ec_double(P, p, a):
    X, Y, Z = P
    if Y == 0 or Z == 0:
        return (0, 1, 0)
    S = 4 * X * Y * Y % p
    M = (3 * X * X + a * pow(Z, 4, p)) % p
    X3 = (M * M - 2 * S) % p
    return (X3, (M * (S - X3) - 8 * pow(Y, 4, p)) % p, 2 * Y * Z % p)


def _ec_add(P, Q, p, a):
    if P[2] == 0:
        return Q
    if Q[2] == 0:
        return P
    Z1s, Z2s = P[2] * P[2] % p, Q[2] * Q[2] % p
    U1, U2 = P[0] * Z2s % p, Q[0] * Z1s % p
    S1, S2 = P[1] * Z2s * Q[2] % p, Q[1] * Z1s * P[2] % p
    if U1 == U2:
        return _ec_double(P, p, a) if S1 == S2 else (0, 1, 0)
    H, R = (U2 - U1) % p, (S2 - S1) % p
    H2 = H * H % p
    H3 = H2 * H % p
    X3 = (R * R - H3 - 2 * U1 * H2) % p
    return (X3, (R * (U1 * H2 - X3) - S1 * H3) % p, H * P[2] * Q[2] % p)


def _ec_mul(k, P, p, a):
    out = (0, 1, 0)
    while k:
        if k & 1:
            out = _ec_add(out, P, p, a)
        P = _ec_double(P, p, a)
        k >>= 1
    return out


def ecdsa_verify(curve_oid: str, qx: int, qy: int, signature_der: bytes, digest: bytes) -> bool:
    """SEC 1 §4.1.4 over a NIST curve: the DER (r, s) against the public point."""
    p, a, b, gx, gy, n = _CURVES[curve_oid]
    if (qy * qy - (qx * qx * qx + a * qx + b)) % p != 0:
        return False
    rs = _items(_der(signature_der)[1])
    r, s_ = (int.from_bytes(x[1], "big") for x in rs[:2])
    if not (1 <= r < n and 1 <= s_ < n) or len(rs) != 2:
        return False
    e = int.from_bytes(digest, "big") >> max(0, 8 * len(digest) - n.bit_length())
    w = pow(s_, -1, n)
    X = _ec_add(_ec_mul(e * w % n, (gx, gy, 1), p, a), _ec_mul(r * w % n, (qx, qy, 1), p, a), p, a)
    if X[2] == 0:
        return False
    return (X[0] * pow(X[2] * X[2], -1, p)) % p % n == r
_OID_SIGNED_DATA, _OID_TST_INFO = "1.2.840.113549.1.7.2", "1.2.840.113549.1.9.16.1.4"
_OID_CONTENT_TYPE, _OID_MESSAGE_DIGEST = "1.2.840.113549.1.9.3", "1.2.840.113549.1.9.4"


def _der(data: bytes, pos: int = 0):
    """(tag, content, raw, next) of the TLV at `pos`: definite lengths only, as DER requires."""
    start = pos
    tag, length = data[pos], data[pos + 1]
    pos += 2
    if tag & 0x1F == 0x1F:
        raise ValueError("a high tag number, which nothing here uses")
    if length & 0x80:
        n = length & 0x7F
        if n == 0 or n > 4:
            raise ValueError("an indefinite or oversized length")
        length, pos = int.from_bytes(data[pos:pos + n], "big"), pos + n
    if pos + length > len(data):
        raise ValueError("a truncated element")
    return tag, data[pos:pos + length], data[start:pos + length], pos + length


def _items(content: bytes) -> list:
    out, pos = [], 0
    while pos < len(content):
        tag, body, raw, pos = _der(content, pos)
        out.append((tag, body, raw))
    return out


def _oid(body: bytes) -> str:
    first, parts, value = body[0], [], 0
    parts += [first // 40, first % 40] if first < 80 else [2, first - 80]
    for b in body[1:]:
        value = (value << 7) | (b & 0x7F)
        if not b & 0x80:
            parts.append(value)
            value = 0
    return ".".join(str(x) for x in parts)


def _as_saved(data: bytes) -> bytes:
    """A certificate file as a person saved it: DER as it is; a PEM in UTF-8 with or without a BOM, or in UTF-16
    (Windows PowerShell 5.1's `>` writes it little-endian with a BOM), with or without the BOM, as ASCII."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:]
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16").encode("ascii", "replace")
    if not data.startswith(b"\x30"):                   # DER is a SEQUENCE; anything else may be UTF-16 with no BOM
        for encoding in ("utf-16-le", "utf-16-be"):     # decoded, so an odd offset in the other order never matches
            text = data.decode(encoding, "replace")
            if "-----BEGIN" in text:
                return text.encode("ascii", "replace")
    return data


def _public_key(pem_or_der: bytes):
    """An X.509 certificate's subject key, PEM or DER: ("rsa", n, e) or ("ec", curve_oid, x, y)."""
    pem_or_der = _as_saved(pem_or_der)
    text = pem_or_der.decode("ascii", "replace") if b"-----BEGIN" in pem_or_der else None
    der = base64.b64decode("".join(l for l in text.split("-----END")[0].splitlines()
                                   if l and not l.startswith("-----"))) if text else pem_or_der
    _t, cert, _r, _n = _der(der)
    tbs = _items(_items(cert)[0][1])
    fields = tbs[1:] if tbs[0][0] == 0xA0 else tbs           # [0] version, when present
    spki = _items(fields[5][1])                              # serial, signature, issuer, validity, subject, spki
    algorithm = _items(spki[0][1])
    bits = spki[1][1][1:]                                    # a BIT STRING's first byte counts unused bits
    kind = _oid(algorithm[0][1])
    if kind == "1.2.840.113549.1.1.1":
        n, e = (int.from_bytes(x[1], "big") for x in _items(_der(bits)[1]))
        return ("rsa", n, e)
    if kind == "1.2.840.10045.2.1":
        curve = _oid(algorithm[1][1])
        if curve not in _CURVES:
            raise ValueError(f"the certificate's curve {curve} is not one this verifier knows")
        size = (_CURVES[curve][0].bit_length() + 7) // 8
        if bits[0] == 4 and len(bits) == 1 + 2 * size:
            return ("ec", curve, int.from_bytes(bits[1:1 + size], "big"), int.from_bytes(bits[1 + size:], "big"))
        if bits[0] in (2, 3) and len(bits) == 1 + size:
            p, a_, b_ = _CURVES[curve][:3]
            x = int.from_bytes(bits[1:], "big")
            y = pow((x * x * x + a_ * x + b_) % p, (p + 1) // 4, p)
            return ("ec", curve, x, y if y % 2 == bits[0] - 2 else p - y)
        raise ValueError("the certificate's EC point is malformed")
    raise ValueError(f"the certificate's key ({kind}) is neither RSA nor EC")


def _pkcs1v15_holds(n: int, e: int, signature: bytes, digest_oid: str, digest: bytes) -> bool:
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    em = pow(int.from_bytes(signature, "big"), e, n).to_bytes(k, "big")
    oid_body = bytes.fromhex({"sha256": "608648016503040201", "sha384": "608648016503040202",
                              "sha512": "608648016503040203"}[_DIGESTS[digest_oid]])

    def info(with_null: bool) -> bytes:
        alg = b"\x06" + bytes([len(oid_body)]) + oid_body + (b"\x05\x00" if with_null else b"")
        alg = b"\x30" + bytes([len(alg)]) + alg
        body = alg + b"\x04" + bytes([len(digest)]) + digest
        return b"\x30" + bytes([len(body)]) + body
    return any(em == b"\x00\x01" + b"\xff" * (k - 3 - len(t)) + b"\x00" + t for t in (info(True), info(False)))


def verify_anchor(anchor: dict, block_hash: str, tsa_cert=None):
    """(failures, notes, generation time) for an RFC 3161 receipt that should
    date `block_hash`. Without the authority's certificate the receipt's
    signature cannot be checked, and the result says so."""
    failures, notes = [], []
    try:
        _t, resp, _r, _n = _der(base64.b64decode(anchor["token"]))
        parts = _items(resp)
        status = int.from_bytes(_items(parts[0][1])[0][1], "big")
        if status not in (0, 1) or len(parts) < 2:
            return [f"anchor_invalid: the authority did not grant the stamp (status {status})"], notes, None
        info = _items(parts[1][1])
        if _oid(info[0][1]) != _OID_SIGNED_DATA:
            return ["anchor_invalid: the token is not a CMS SignedData"], notes, None
        signed = _items(_der(info[1][1])[1])
        encap = _items(signed[2][1])
        if _oid(encap[0][1]) != _OID_TST_INFO:
            return ["anchor_invalid: the token's content is not a TSTInfo"], notes, None
        tst_der = _der(encap[1][1])[1]
        tst = _items(_der(tst_der)[1])
        imprint = _items(tst[2][1])
        if _DIGESTS.get(_oid(_items(imprint[0][1])[0][1])) != "sha256" or imprint[1][1] != bytes.fromhex(block_hash):
            return ["anchor_invalid: the stamp's imprint is not this block's hash"], notes, None
        gen_time = tst[4][1].decode("ascii")
        signer = _items(_items(signed[-1][1])[0][1])
        digest_oid = _oid(_items(signer[2][1])[0][1])
        attrs_raw = next(raw for tag, _b, raw in signer if tag == 0xA0)
        attrs = {}
        for _tag, body, _raw in _items(_der(attrs_raw)[1]):
            pair = _items(body)
            attrs[_oid(pair[0][1])] = _items(pair[1][1])[0]
        if digest_oid not in _DIGESTS:
            return [f"anchor_invalid: an unknown digest {digest_oid}"], notes, gen_time
        if _oid(attrs.get(_OID_CONTENT_TYPE, (0, b"\x00", b""))[1]) != _OID_TST_INFO \
                or attrs.get(_OID_MESSAGE_DIGEST, (0, b"", b""))[1] != hashlib.new(_DIGESTS[digest_oid], tst_der).digest():
            return ["anchor_invalid: the signed attributes do not hold the TSTInfo's digest"], notes, gen_time
        if tsa_cert is None:
            notes.append("anchor_unpinned: give the authority's certificate (--tsa-cert) to check the stamp's "
                         f"signature; the stamp claims {gen_time}")
            return failures, notes, gen_time
        sig_oid = _oid(_items(signer[4][1])[0][1])
        try:
            key = _public_key(tsa_cert)
        except (KeyError, IndexError, TypeError, ValueError, StopIteration, OverflowError, RecursionError) as e:
            return [f"anchor_invalid: the authority's certificate (--tsa-cert) cannot be read ({type(e).__name__})"], \
                notes, gen_time
        signed_attrs = b"\x31" + attrs_raw[1:]
        if sig_oid in _RSA_SIGNATURES and key[0] == "rsa":
            hash_name = _RSA_HASHES.get(sig_oid, _DIGESTS[digest_oid])
            hash_oid = next(o for o, h in _DIGESTS.items() if h == hash_name)
            holds = _pkcs1v15_holds(key[1], key[2], signer[5][1], hash_oid,
                                    hashlib.new(hash_name, signed_attrs).digest())
        elif sig_oid in _ECDSA_SIGNATURES and key[0] == "ec":
            holds = ecdsa_verify(key[1], key[2], key[3], signer[5][1],
                                 hashlib.new(_ECDSA_SIGNATURES[sig_oid], signed_attrs).digest())
        else:
            return [f"anchor_invalid: the stamp's signature ({sig_oid}) does not match the certificate's key "
                    f"({key[0]})"], notes, gen_time
        if not holds:
            return ["anchor_invalid: the stamp's signature is not the authority's"], notes, gen_time
        notes.append(f"anchored: {anchor.get('authority', 'the authority')} dated block "
                     f"{anchor.get('block_index')} at {gen_time}")
        return failures, notes, gen_time
    except (KeyError, IndexError, TypeError, ValueError, StopIteration, OverflowError, RecursionError) as e:
        return [f"anchor_invalid: the receipt cannot be read ({type(e).__name__})"], notes, None


# ---------- the credential ----------

def _start(key: dict):
    """Where a key's range opens: the block that announced it, or, for a key still pending, the block that will."""
    return key.get("announced_in") if key.get("announced_in") is not None else key.get("from_block")


def _outside(index: int, issued: bool, periods: list) -> str:
    """Why a signature by a valid key is not verified, with the only evidence that would show it was made while the
    key was active. It never suggests another identity to check against: an identity that left the key's range
    open is exactly the one a copy of the key would be checked against."""
    spans = []
    for k in periods:
        start, until = (None if issued else _start(k)), k.get("until_block")
        spans.append(f"blocks {start} to {until - 1}" if start is not None and until is not None
                     else f"blocks {start} onward" if start is not None
                     else "every block" if until is None
                     else f"blocks before {until}" if issued else f"an end at block {until} and no start")
    head = (f"signature_outside_key_period: block {index} is outside the range of blocks its key was active for in "
            f"the key list it was checked against ({', '.join(spans)})"
            f"{'; the signature was made at issue' if issued else ''}. ")
    starts = [_start(k) for k in periods]
    if not issued and starts and all(s is not None and index < s for s in starts):
        return head + "A key signs no block appended before the block that announced it, so this is not verified"
    if not issued and any(s is None and k.get("until_block") is not None and index < k["until_block"]
                          for s, k in zip(starts, periods)):
        return head + ("A period that states where it closed and not where it opened has no blocks that a "
                       "signature made as a block was appended can be held to, so this is not verified")
    closed = ("A key's range closes at the block where its instance moved to a new key, and a closed key's "
              "signature over a later block is what a copy of that key could make, so this is not verified. ")
    if issued:                                      # an anchor dates a block, never a signature made at issue
        return head + closed + ("Only a copy of this credential received and checked before the range closed shows "
                                "the signature was made while the key was active")
    return head + closed + ("Only an anchor that dates the block before the range closed, or a copy of this "
                            "credential received and checked before then, shows the block was sealed while the key "
                            "was active")


def _check_signatures(signatures: list, blocks: list, keys: list, failures: list, notes: list) -> set:
    """The hashes of the blocks a valid signature covers; each failure named into `failures`. A signature is
    matched to a block by its index AND its hash, never by the index alone."""
    covered = set()
    why = _keys_problem(keys)
    if why:
        failures.append(f"malformed: {why}")
        return covered
    for sig in signatures:
        index = sig.get("block_index")
        block = next((b for b in blocks if b.get("index") == index and b.get("block_hash") == sig.get("block_hash")),
                     None)
        if block is None:
            failures.append(f"signature_invalid: a signature names block {index}, which this document's proof does "
                            "not rest on")
            continue
        periods = [k for k in keys if k.get("key_id") == sig.get("key_id")]
        if not periods:
            failures.append(f"signature_invalid: block {index} is signed by a key not on the list")
            continue
        if hashlib.sha256(_hex(periods[0]["public_key"])).hexdigest() != sig.get("key_id"):
            failures.append(f"key_id_mismatch: block {index}'s key id is not the hash of the public key listed "
                            "under it")
            continue
        statement = STATEMENT_PREFIX + int(index).to_bytes(8, "big") + _hex(block["block_hash"])
        if not ed25519_verify(_hex(periods[0]["public_key"]), statement, _hex(sig["value"])):
            failures.append(f"signature_invalid: block {index}'s signature does not verify")
            continue
        at = int(index)
        if sig.get("signed") == "issue":
            # Made when the credential was issued, over a block appended before its instance signed blocks: the
            # key must still have been active once that block existed, so the block falls before its range closed.
            if not any(k.get("until_block") is None or at < k["until_block"] for k in periods):
                failures.append(_outside(at, True, periods))
                continue
            notes.append(f"signed_at_issue: block {index} was sealed before its instance signed blocks; "
                         "its signature was made when the credential was issued")
        else:                                       # `seal`, or any other label or none: the strict reading
            anywhere = any(_start(k) is None and k.get("until_block") is None for k in periods)
            inside = any(_start(k) is not None and _start(k) <= at
                         and (k.get("until_block") is None or at < k["until_block"]) for k in periods)
            if not (inside or anywhere):
                failures.append(_outside(at, False, periods))
                continue
        covered.add(block["block_hash"])
    return covered


def _distinct(blocks: list, failures: list) -> None:
    """Two different blocks claiming one index make the document malformed: a signature or an anchor for one
    must never stand for the other."""
    seen = {}
    for b in blocks:
        if seen.setdefault(b.get("index"), b.get("block_hash")) != b.get("block_hash"):
            failures.append(f"malformed: two different blocks claim index {b.get('index')}")


def _check_anchors(anchors: list, blocks: list, tsa_cert, failures: list, notes: list) -> None:
    if tsa_cert is not None:
        dated = {a.get("block_index") for a in anchors or [] if isinstance(a, dict)}
        for b in blocks:
            if b.get("index") not in dated:
                notes.append(f"no_anchor: --tsa-cert was given and block {b.get('index')} carries no anchor, so "
                             "nothing dates it but the instance's own word")
    for anchor in anchors or []:
        block = next((b for b in blocks if b.get("index") == anchor.get("block_index")), None)
        if block is None:
            failures.append(f"anchor_invalid: an anchor names block {anchor.get('block_index')}, which this "
                            "document's proof does not rest on")
            continue
        f, n, _when = verify_anchor(anchor, block["block_hash"], tsa_cert)
        failures += f
        notes += n


UNPINNED = ("key_unpinned: no --identity or --key was given, so the signatures were checked against the keys this "
            "document lists itself, which says nothing about who sealed it")


def verify_credential(credential: dict, keys=None, tsa_cert=None) -> dict:
    """`{"verified": bool, "failures": [...], "notes": [...], "disclosed": {...}}`.
    `keys` is the pinned key list (`/identity`'s `keys`); None checks the
    signatures against the credential's own list and fails `key_unpinned`."""
    failures, notes = [], []
    try:
        _strict(credential, "credential")
        if credential.get("credential") != "nexus.credential" or credential.get("version") != VERSION:
            return {"verified": False, "failures": [f"malformed: not a version-{VERSION} NEXUS credential"],
                    "notes": [], "disclosed": {}}
        commitments = {n: _hex(c) for n, c in credential["commitments"].items()}
        disclosed = credential.get("disclosed") or {}
        opened = set()
        for name in sorted(disclosed):
            entry = disclosed[name]
            salt = _hex(entry.get("salt") or "")
            if name not in commitments:
                failures.append(f"disclosure_mismatch: {name} is disclosed but has no commitment")
                continue
            bare = hashlib.sha256(canonical(entry["value"])).digest()
            if len(salt) != SALT_BYTES or commitments[name] == bare:
                failures.append(f"unsalted_hash: {name} is not committed under a {SALT_BYTES}-byte salt")
                continue
            if commitment(salt, entry["value"]) != commitments[name]:
                failures.append(f"disclosure_mismatch: the value and salt of {name} do not open its commitment")
                continue
            opened.add(name)
        _check_record(commitments, disclosed, opened, credential["proof"].get("record_hash"), failures, notes)
        root = credential["commitment_root"]
        if commitment_root(commitments).hex() != root:
            failures.append("commitments_mismatch: the field commitments do not make the commitment root")
        proof = credential["proof"]
        for part in (proof, proof.get("binding")):
            if isinstance(part, dict):               # M-CAN-8, where W8's own check decodes and does not hold it
                _hex(part.get("leaf", ""))
                for element in part.get("path") or []:
                    _hex(element)
        for sealed in _sealed_list(proof.get("block")):          # and a layout-1 block's sealed list, read the same
            _hex(sealed)
        why = verify_proof(proof)
        if why:
            failures.append(why)
        for part in (proof, proof.get("binding")):
            if isinstance(part, dict) and "lineage_id" in part and (
                    "lineage_id" not in opened or disclosed["lineage_id"]["value"] != part["lineage_id"]):
                failures.append("lineage_id_mismatch: the proof names a record, and the credential does not "
                                "disclose that name")
                break
        sealed_root = proof.get("commitment_root") or (proof.get("binding") or {}).get("commitment_root")
        if sealed_root != root:
            failures.append("root_not_sealed: the commitment root is not the one the record's seal or binding holds")
        named = credential.get("decision_fields") or []
        if not isinstance(named, list) or any(f not in commitments for f in named):
            failures.append("malformed: a decision field is named that the record does not commit")
        if keys is None:
            keys = credential.get("keys") or []
            failures.append(UNPINNED)
        blocks = [proof["block"]] + ([proof["binding"]["block"]] if "binding" in proof else [])
        if blocks[0].get("layout") not in (1, 2):
            failures.append(f"block_malformed: block {blocks[0].get('index')}: a proof rests on a layout-2 block, "
                            "or on the older layout 1")
        if len(blocks) == 2 and blocks[1].get("layout") != 2:   # W8's check reads its header whatever it is called
            failures.append(f"block_malformed: block {blocks[1].get('index')}: a binding rests on a layout-2 block")
        _distinct(blocks, failures)
        _check_headers(blocks, failures)
        covered = _check_signatures(credential.get("signatures") or [], blocks, keys, failures, notes)
        for b in blocks:
            if b["block_hash"] not in covered:
                failures.append(f"unsigned: no valid signature covers block {b['index']}")
        _check_anchors(credential.get("anchors"), blocks, tsa_cert, failures, notes)
        return {"verified": not failures, "failures": failures, "notes": notes,
                "disclosed": {n: disclosed[n]["value"] for n in sorted(disclosed)}}
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError, RecursionError) as e:
        return {"verified": False, "failures": failures + [f"malformed: {type(e).__name__}: {e}"], "notes": notes,
                "disclosed": {}}


def verify_reveal(receipt: dict, content: str, salt_hex: str, keys=None, tsa_cert=None) -> dict:
    """W9 (D8) — a later disclosure of a held commitment: the content and its
    salt open the commitment, which is a leaf of the block's `commit` root,
    whose block the instance signed. The block's time is the instance's word
    until an anchor receipt verifies."""
    failures, notes = [], []
    try:
        _strict(receipt, "receipt")
        if receipt.get("receipt") != "nexus.commitment" or receipt.get("version") != VERSION:
            return {"verified": False, "failures": [f"malformed: not a version-{VERSION} NEXUS commitment receipt"],
                    "notes": [], "held_at": None}
        salt = _hex(salt_hex or "")
        if len(salt) != SALT_BYTES:
            failures.append(f"unsalted_hash: the salt is not {SALT_BYTES} bytes")
        elif commitment(salt, content).hex() != receipt["commitment"]:
            failures.append("reveal_mismatch: the content and salt do not open the held commitment")
        block, header = receipt["block"], receipt["block"]["header"]
        if block.get("layout") != 2:
            failures.append(f"block_malformed: block {block.get('index')}: a receipt rests on a layout-2 block")
        elif _canonical_hash(header) != block["block_hash"] or header.get("index") != block["index"]:
            failures.append("block_hash_mismatch: the header does not hash to the block's hash")
        else:
            entry = header["roots"]["commit"]
            if receipt["tree_size"] != entry["size"]:
                failures.append("size_mismatch: the tree's size is not the size the header seals")
            if _is_hash(entry["root"]):              # a root that is not a hash is the header's to name, below
                why = verify_inclusion(_hex(receipt["commitment"]), int(receipt["leaf_index"]),
                                       int(receipt["tree_size"]), [_hex(p) for p in receipt["path"]],
                                       bytes.fromhex(entry["root"]))
                if why:
                    failures.append(why)
        if keys is None:
            keys = receipt.get("keys") or []
            failures.append(UNPINNED)
        _check_headers([block], failures)
        covered = _check_signatures(receipt.get("signatures") or [], [block], keys, failures, notes)
        if block["block_hash"] not in covered:
            failures.append(f"unsigned: no valid signature covers block {block['index']}")
        _check_anchors(receipt.get("anchors"), [block], tsa_cert, failures, notes)
        return {"verified": not failures, "failures": failures, "notes": notes,
                "held_at": header.get("timestamp")}
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError, RecursionError) as e:
        return {"verified": False, "failures": failures + [f"malformed: {type(e).__name__}: {e}"], "notes": notes,
                "held_at": None}


# W10b (D7) — the fields an action's record hash is over, in this order: this file's own copy of
# `backend/memory/actions.py`'s HASHED_FIELDS (it imports nothing from NEXUS; a test holds the two equal).
ACTION_FIELDS = ("action_id", "kind", "evidence_class", "method", "target", "request_hash", "credentials_json",
                 "lineage_id", "trigger_route", "identity_commitment", "identity_mechanism", "signature_key_id",
                 "signature", "requested_at", "state", "status", "response_hash", "completed_at")


def verify_action(receipt: dict, keys=None, tsa_cert=None) -> dict:
    """W10b (D7) — a governed action: its fields hash to its record hash, which is a leaf of the sealing
    block's `action` root, whose block the instance signed. What it proves: the instance recorded this action,
    with this outcome, by its key. Not that the request did what its kind says, and not when, until an anchor
    receipt verifies."""
    failures, notes = [], []
    try:
        _strict(receipt, "receipt")
        if receipt.get("receipt") != "nexus.action" or receipt.get("version") != VERSION:
            return {"verified": False, "failures": [f"malformed: not a version-{VERSION} NEXUS action receipt"],
                    "notes": [], "action": None}
        fields = receipt["fields"]
        if not isinstance(fields, dict) or set(fields) != set(ACTION_FIELDS):
            return {"verified": False, "failures": ["malformed: the action's fields are not the set its hash is "
                                                    "over"], "notes": [], "action": None}
        if receipt.get("action_id") != fields["action_id"]:
            return {"verified": False, "failures": ["malformed: the receipt's action id is not the one among its "
                                                    "fields"], "notes": [], "action": None}
        record_hash = hashlib.sha256(canonical({k: fields[k] for k in ACTION_FIELDS})).hexdigest()
        if record_hash != receipt["record_hash"]:
            failures.append("action_mismatch: the action's fields do not hash to its record hash")
        person = (receipt.get("disclosed") or {}).get("accountable_identity")
        if person is not None:
            salt = _hex(person.get("salt") or "")
            if len(salt) != SALT_BYTES or commitment(salt, person.get("value")).hex() != fields["identity_commitment"]:
                failures.append("disclosure_mismatch: the accountable identity and salt do not open its commitment")
            else:
                notes.append(f"accountable: {person.get('value')!r} ({fields['identity_mechanism'] or 'asserted'}"
                             "), disclosed")
        else:
            notes.append("accountable: committed, not disclosed (the instance's gated receipt discloses it)")
        block, header = receipt["block"], receipt["block"]["header"]
        if block.get("layout") != 2:
            failures.append(f"block_malformed: block {block.get('index')}: a receipt rests on a layout-2 block")
        elif _canonical_hash(header) != block["block_hash"] or header.get("index") != block["index"]:
            failures.append("block_hash_mismatch: the header does not hash to the block's hash")
        else:
            entry = header["roots"]["action"]
            if receipt["tree_size"] != entry["size"]:
                failures.append("size_mismatch: the tree's size is not the size the header seals")
            if _is_hash(entry["root"]):              # a root that is not a hash is the header's to name, below
                why = verify_inclusion(_hex(receipt["record_hash"]), int(receipt["leaf_index"]),
                                       int(receipt["tree_size"]), [_hex(p) for p in receipt["path"]],
                                       bytes.fromhex(entry["root"]))
                if why:
                    failures.append(why)
        if keys is None:
            keys = receipt.get("keys") or []
            failures.append(UNPINNED)
        _check_headers([block], failures)
        covered = _check_signatures(receipt.get("signatures") or [], [block], keys, failures, notes)
        if block["block_hash"] not in covered:
            failures.append(f"unsigned: no valid signature covers block {block['index']}")
        _check_anchors(receipt.get("anchors"), [block], tsa_cert, failures, notes)
        notes.append(f"action: {fields['evidence_class']} {fields['kind']} {fields['method']} {fields['target']}, "
                     f"{fields['state']} (status {fields['status']})"
                     + ("; the client reported it: NEXUS did not see it happen"
                        if fields["evidence_class"] == "client_reported" else "")
                     # W10c — what an interceptor saw of a tool use: the asking and the report, never the running
                     + ("; the model asked for it and the harness reported a result: the recorder did not see "
                        "the tool run" if fields["evidence_class"] == "intercepted" else ""))
        return {"verified": not failures, "failures": failures, "notes": notes, "action": fields}
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError, RecursionError) as e:
        return {"verified": False, "failures": failures + [f"malformed: {type(e).__name__}: {e}"], "notes": notes,
                "action": None}


def verify_chain(document: dict, keys=None, tsa_cert=None) -> dict:
    """W11d (docs/TALIMA_SPEC.md, section 11) — a chain document: each block's header and hash, the indices in
    order, each link and block 0's, each root whose leaves are listed, and a signature for every block. What it
    proves: these blocks are one chain, sealed by the key pinned. Not that the chain is the producer's whole
    chain (a document may begin after block 0, and says so), and not when, until an anchor verifies."""
    failures, notes = [], []
    out = {"blocks": 0, "first_index": None, "head": None}
    try:
        _strict(document, "chain")
        if document.get("chain") != CHAIN or document.get("version") != VERSION:
            return {"verified": False, "failures": [f"malformed: not a version-{VERSION} chain document"],
                    "notes": [], **out}
        blocks = document["blocks"]
        if not isinstance(blocks, list) or not blocks or not all(isinstance(b, dict) for b in blocks):
            return {"verified": False, "failures": ["malformed: a chain document holds at least one block"],
                    "notes": [], **out}
        first, listed = blocks[0].get("index"), 0
        for position, block in enumerate(blocks):
            index, header = block.get("index"), block.get("header")
            why = "a chain document holds layout-2 blocks" if block.get("layout") != 2 else _header_problem(header)
            if why:
                failures.append(f"block_malformed: block {index}: {why}")
                continue
            if _canonical_hash(header) != block.get("block_hash") or header["index"] != index:
                failures.append(f"block_hash_mismatch: block {index}'s header does not hash to its block hash")
            if position == 0:
                if index == 0 and header["previous_hash"] != GENESIS_HASH:
                    failures.append("chain_link_broken: block 0's previous hash is not 64 zeros")
                elif index != 0 and header["previous_hash"] == GENESIS_HASH:
                    failures.append(f"chain_link_broken: block {index} is not block 0 and names no block before it")
                elif index != 0:
                    notes.append(f"partial_chain: the document begins at block {index}; nothing before it is checked")
            else:
                before = blocks[position - 1]
                if not _is("integer", first) or index != first + position:
                    failures.append(f"index_gap: block {index} follows block {before.get('index')}; the indices "
                                    "do not run in order with none missing")
                if header["previous_hash"] != before.get("block_hash"):
                    failures.append(f"chain_link_broken: block {index}'s previous hash is not the hash of the "
                                    "block before it")
            for name in sorted(block.get("leaves") or {}):
                entry = header["roots"].get(name)
                if entry is None:
                    failures.append(f"roots_mismatch: block {index} lists leaves for {name!r}, a root its header "
                                    "does not seal")
                    continue
                leaves = [_hex(x) for x in block["leaves"][name]]
                listed += 1
                if name in LEAF_BYTES and any(len(leaf) not in LEAF_BYTES[name] for leaf in leaves):
                    failures.append(f"leaf_malformed: block {index} lists a {name} leaf that is not "
                                    f"{' or '.join(str(n) for n in LEAF_BYTES[name])} bytes")
                    continue
                if name == "lineage" and any(len(leaf) == 32 for leaf in leaves):
                    notes.append(f"bare_hash_leaves: block {index} sealed a record as its bare hash, the older "
                                 "form: its commitment root is held by a later binding, if at all")
                if len(leaves) != entry["size"]:
                    failures.append(f"size_mismatch: block {index}'s {name} root is over {entry['size']} leaves "
                                    f"and {len(leaves)} are listed")
                elif merkle_root(leaves).hex() != entry["root"]:
                    failures.append(f"roots_mismatch: block {index}'s {name} root is not the root of the leaves "
                                    "listed for it")
        sealed = sum(len(b["header"]["roots"]) for b in blocks
                     if isinstance(b.get("header"), dict) and isinstance(b["header"].get("roots"), dict))
        if listed != sealed:
            notes.append(f"leaves_not_listed: {sealed - listed} of the {sealed} roots these blocks seal list no "
                         "leaves here, and are taken as sealed")
        if keys is None:
            keys = document.get("keys") or []
            failures.append(UNPINNED)
        _distinct(blocks, failures)
        covered = _check_signatures(document.get("signatures") or [], blocks, keys, failures, notes)
        for b in blocks:
            if b.get("block_hash") not in covered:
                failures.append(f"unsigned: no valid signature covers block {b.get('index')}")
        _check_anchors(document.get("anchors"), blocks, tsa_cert, failures, notes)
        return {"verified": not failures, "failures": failures, "notes": notes, "blocks": len(blocks),
                "first_index": first, "head": blocks[-1].get("block_hash")}
    except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError, RecursionError) as e:
        return {"verified": False, "failures": failures + [f"malformed: {type(e).__name__}: {e}"], "notes": notes,
                **out}


def read_document(path: str):
    """A JSON document as a person saved it: UTF-8, with or without a BOM, or UTF-16 with its BOM (Windows
    PowerShell 5.1's `>`). A note to reveal is never read this way: it is read exactly."""
    with open(path, "rb") as f:
        raw = f.read()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig")
    return strict_json(text)


def _not_json(token: str):
    raise ValueError(f"{token} is not a JSON value; a document carries none (M-CAN-5)")


def _finite(text: str) -> float:
    value = float(text)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"the number {text[:24]} is past what a document carries (M-CAN-5)")
    return value


def _once(pairs: list) -> dict:
    out = {}
    for name, value in pairs:
        if name in out:
            raise ValueError(f"the name {name!r} is given twice in one object (M-CAN-6)")
        out[name] = value
    return out


def _whole_characters(value) -> None:
    """Raise on a string that holds half of a surrogate pair, anywhere in the document (M-CAN-7)."""
    todo = [value]
    while todo:
        item = todo.pop()
        if isinstance(item, str):
            if any("\ud800" <= c <= "\udfff" for c in item):
                raise ValueError("a string holds an unpaired surrogate (M-CAN-7)")
        elif isinstance(item, dict):
            todo.extend(item)
            todo.extend(item.values())
        elif isinstance(item, list):
            todo.extend(item)


def strict_json(text: str):
    """A document's text as section 3 lets it be read: no NaN or infinity, written or reached by a number too
    large; no name given twice in one object; no half of a character pair."""
    document = json.loads(text, parse_constant=_not_json, parse_float=_finite, object_pairs_hook=_once)
    _whole_characters(document)
    return document


def force_utf8_stdio() -> None:
    """This file's own copy of `scripts/_console.py`'s helper (it imports nothing from NEXUS): stdout and stderr
    re-encoded as UTF-8 with `errors="replace"`, so piped output reads the same on every platform, and an isolated
    run (`-I` ignores PYTHONIOENCODING) prints a character the console's code page lacks instead of failing."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


def invoked_name(argv0: str) -> str:
    """W11b (run/specs/W11b.md D4): the name the verifier was started as. Talima is the open record and verification
    standard and NEXUS its reference engine, so the one verifier has two names: `talima-verify` for a file whose
    name begins `talima` (a release's binary is named for itself), otherwise `nexus-verify`."""
    return "talima-verify" if os.path.basename(argv0).lower().startswith("talima") else "nexus-verify"


def _typed(text: str) -> str:
    """What a person typed after `--key` or `--salt`: the spaces and the case are theirs, and no document's."""
    return "".join(text.split()).lower()


def main(argv=None, name=None) -> int:
    force_utf8_stdio()
    prog = name or invoked_name(sys.argv[0] if sys.argv else "")
    ap = argparse.ArgumentParser(prog=prog, description=__doc__.split("\n\n")[0].replace("nexus-verify", prog, 1))
    ap.add_argument("credential", nargs="?", help="the credential, a JSON file")
    ap.add_argument("--reveal", metavar="RECEIPT", help="a held commitment's receipt, a JSON file, to reveal")
    ap.add_argument("--content", help="with --reveal: the file whose text was committed (read as UTF-8, exactly)")
    ap.add_argument("--salt", help="with --reveal: the salt /commit handed back, 64 hex characters")
    ap.add_argument("--action", metavar="ACTION", help="a governed action's receipt (GET /actions/{id}), a JSON file")
    ap.add_argument("--chain", metavar="CHAIN", help="a chain document (docs/TALIMA_SPEC.md, section 11), a JSON file")
    pin = ap.add_mutually_exclusive_group()
    pin.add_argument("--identity", help="the instance's identity (the body of its GET /identity), a JSON file")
    pin.add_argument("--key", help="one public key, 64 hex characters, active at any time")
    ap.add_argument("--tsa-cert", help="the time-stamping authority's certificate (PEM or DER), to check anchors")
    ap.add_argument("--json", action="store_true", help="print the result as JSON")
    args = ap.parse_args(argv)
    if args.key is not None:
        args.key = _typed(args.key)
        if len(args.key) != 64 or any(c not in "0123456789abcdef" for c in args.key):
            ap.error("--key is one public key, 64 hexadecimal characters")
    if args.reveal and args.salt is not None:       # beside any other document a salt is not read
        args.salt = _typed(args.salt)
        if len(args.salt) % 2 or any(c not in "0123456789abcdef" for c in args.salt):
            ap.error("--salt is the salt the commitment was made with, in hexadecimal")
    if sum(bool(x) for x in (args.reveal, args.credential, args.action, args.chain)) != 1 \
            or (args.reveal and not (args.content and args.salt)):
        ap.error("give a credential, or --reveal RECEIPT with --content and --salt, or --action ACTION, or "
                 "--chain CHAIN")
    try:
        if args.chain:
            document = read_document(args.chain)
        elif args.action:
            document = read_document(args.action)
        elif args.reveal:
            document = read_document(args.reveal)
            with open(args.content, encoding="utf-8", newline="") as f:
                content = f.read()
        else:
            document = read_document(args.credential)
        keys = None
        tsa_cert = None
        if args.tsa_cert:
            with open(args.tsa_cert, "rb") as f:
                tsa_cert = f.read()
        if args.identity:
            keys = read_document(args.identity)["keys"]
            if not isinstance(keys, list):           # None would read as "no identity was given"
                raise ValueError("the identity's keys are not a list: an identity lists its producer's keys")
        elif args.key:
            keys = [{"key_id": hashlib.sha256(bytes.fromhex(args.key)).hexdigest(), "public_key": args.key,
                     "announced_in": None, "until_block": None}]
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as e:
        print(f"{prog}: cannot read the input: {type(e).__name__}: {e}", file=sys.stderr)
        if args.json and not isinstance(e, OSError):
            print(json.dumps({"verified": False, "failures": [f"malformed: {type(e).__name__}: {e}"], "notes": []},
                             sort_keys=True, indent=2))
        return 2
    result = (verify_chain(document, keys, tsa_cert) if args.chain
              else verify_action(document, keys, tsa_cert) if args.action
              else verify_reveal(document, content, args.salt, keys, tsa_cert) if args.reveal
              else verify_credential(document, keys, tsa_cert))
    if args.json:
        print(json.dumps(result, sort_keys=True, indent=2))
    else:
        for line in result["failures"]:
            print(f"FAIL {line}")
        for line in result["notes"]:
            print(f"NOTE {line}")
        for name, value in (result.get("disclosed") or {}).items():
            print(f"DISCLOSED {name} = {json.dumps(value, sort_keys=True)}")
        if args.reveal and result["verified"]:
            print(f"HELD by the block's own time {result['held_at']} (the instance's word; an anchor dates it)")
        if args.chain and result["verified"]:
            print(f"CHAIN {result['blocks']} block(s) from index {result['first_index']}, head {result['head']}")
        print(f"{prog}: VERIFIED" if result["verified"] else
              f"{prog}: NOT VERIFIED ({len(result['failures'])} failure(s))")
    return 0 if result["verified"] else 1


if __name__ == "__main__":
    sys.exit(main())
