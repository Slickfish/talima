"""W8 — salted field commitments (run/specs/W8.md, D4).

WHAT THIS IS
    One commitment per field of a record's hashed body:
    C_f = SHA-256(salt_f || canonical(value_f)), with `salt_f` 32 random bytes
    kept beside the record, never in it or its hash. The commitment root is
    the RFC 6962 root (`backend/memory/merkle.py`) over the leaves
    `name || 0x00 || C_f`, in field-name order. An opening is a value and its
    salt; it is checked against the commitment under that field's name.

WHY SALTED
    A record's hashed body carries unsalted hashes of short content (the
    question's, the answer's): an answer of "yes" is recovered from its hash
    by trying candidates. A commitment under a fresh 32-byte salt cannot be
    matched that way, so a credential (W9) can open the fields it discloses
    and leave the rest as commitments that say nothing.

WHAT IT DOES NOT DO
    It knows nothing of the store: which body a record has, where the salts
    are kept and when, are the lineage store's.
"""
from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass

from backend.memory import merkle

SALT_BYTES = 32
REFUSALS = ("no_commitment", "malformed", "salt_or_value_mismatch")


def canonical(value) -> bytes:
    """The bytes a field's value is committed as: canonical JSON, sorted keys,
    compact separators, ASCII, no NaN (a value JSON cannot carry is refused,
    never written as something a reader would parse differently)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("ascii")


def commitment(salt: bytes, value) -> bytes:
    return hashlib.sha256(salt + canonical(value)).digest()


def leaf(name: str, c: bytes) -> bytes:
    return name.encode("utf-8") + b"\x00" + c


def commitment_root(commitments: dict) -> bytes:
    return merkle.root([leaf(name, commitments[name]) for name in sorted(commitments)])


@dataclass(frozen=True)
class Commitments:
    salts: dict          # field -> 32 bytes
    commitments: dict    # field -> 32 bytes
    root: bytes


def commit(body: dict, salts: dict | None = None) -> Commitments:
    """The commitments of every field of `body`, under `salts` when given
    (to recompute) or fresh ones. A given salt must be exactly SALT_BYTES
    bytes, one per field, or ValueError names the field: a commitment has no
    separator, so a longer or shorter salt moves bytes between the salt and
    the value (W8's third delta check, MF-1: a 33-byte salt ending in "-"
    rebuilds a root whose commitment opens, under the 32-byte salt, to the
    value negated)."""
    if salts is None:
        salts = {name: secrets.token_bytes(SALT_BYTES) for name in body}
    else:
        salts = dict(salts)
        for name in body:
            if not isinstance(salts.get(name), (bytes, bytearray)) or len(salts[name]) != SALT_BYTES:
                raise ValueError(f"the salt of {name!r} is missing or not {SALT_BYTES} bytes")
    made = {name: commitment(salts[name], body[name]) for name in body}
    return Commitments(salts={name: salts[name] for name in body}, commitments=made, root=commitment_root(made))


def open_field(name: str, value, salt: bytes, commitments: dict) -> str | None:
    """None when `value` under `salt` is what was committed as `name`;
    otherwise why not, as one of `REFUSALS` and its detail."""
    if name not in commitments:
        return f"no_commitment: the record commits no field named {name!r}"
    if not isinstance(salt, (bytes, bytearray)) or len(salt) != SALT_BYTES:
        return f"malformed: a salt is {SALT_BYTES} bytes"
    try:
        c = commitment(bytes(salt), value)
    except (TypeError, ValueError) as e:
        return f"malformed: the value is not canonical JSON ({type(e).__name__})"
    if c != commitments[name]:
        return f"salt_or_value_mismatch: the opening of {name!r} is not what was committed"
    return None
