"""W8 — the Merkle tree of RFC 6962 (run/specs/W8.md, D1): roots, audit paths and their verification.

WHAT THIS IS
    The Merkle Tree Hash of RFC 6962 §2.1, with its domain separation: a leaf
    is hashed as SHA-256(0x00 || leaf) and an inner node as
    SHA-256(0x01 || left || right), the tree splitting n leaves at k, the
    largest power of two smaller than n. Without the two prefixes an inner
    node could be presented as a leaf (a second preimage); with them it
    cannot. The audit path is RFC 6962 §2.1.1's, and `verify_inclusion` is
    the verification of RFC 9162 §2.1.3.2, which needs nothing but the leaf,
    its index, the tree's size, the path and the root.

WHAT IT IMPORTS
    `hashlib` and nothing else, so W9's standalone verifier can carry this
    file unchanged and a stranger can check a proof with no NEXUS module.

WHAT IT DOES NOT DO
    It knows nothing of records, blocks or the store: which leaves a block
    seals, in which order, is the chain's and the lineage store's to say.
"""
from __future__ import annotations

import hashlib

LEAF_PREFIX = b"\x00"
NODE_PREFIX = b"\x01"
HASH_SIZE = 32

# Why a proof was refused, by name: the first word of each refusal.
REFUSALS = ("malformed", "index_out_of_range", "path_too_long", "path_too_short", "root_mismatch")


def leaf_hash(leaf: bytes) -> bytes:
    return hashlib.sha256(LEAF_PREFIX + leaf).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(NODE_PREFIX + left + right).digest()


def _split(n: int) -> int:
    """The largest power of two strictly smaller than n (n >= 2)."""
    k = 1
    while k * 2 < n:
        k *= 2
    return k


def root(leaves: list[bytes]) -> bytes:
    """The Merkle Tree Hash of the leaves, in their order. The empty tree's is
    SHA-256 of the empty string, as RFC 6962 defines it."""
    if not leaves:
        return hashlib.sha256(b"").digest()
    level = [leaf_hash(x) for x in leaves]
    return _subtree_root(level)


def _subtree_root(hashes: list[bytes]) -> bytes:
    if len(hashes) == 1:
        return hashes[0]
    k = _split(len(hashes))
    return node_hash(_subtree_root(hashes[:k]), _subtree_root(hashes[k:]))


def audit_path(leaves: list[bytes], index: int) -> list[bytes]:
    """The audit path of the leaf at `index`: the sibling hashes from the leaf
    up to the root, nearest first (RFC 6962 §2.1.1)."""
    if not 0 <= index < len(leaves):
        raise IndexError(f"leaf index {index} outside a tree of {len(leaves)}")
    return _path(index, [leaf_hash(x) for x in leaves])


def _path(m: int, hashes: list[bytes]) -> list[bytes]:
    if len(hashes) <= 1:
        return []
    k = _split(len(hashes))
    if m < k:
        return _path(m, hashes[:k]) + [_subtree_root(hashes[k:])]
    return _path(m - k, hashes[k:]) + [_subtree_root(hashes[:k])]


def verify_inclusion(leaf: bytes, index: int, size: int, path: list[bytes], expected_root: bytes) -> str | None:
    """None when `path` proves `leaf` is the leaf at `index` of the tree of
    `size` leaves whose root is `expected_root`; otherwise why not, as one of
    `REFUSALS` and its detail. RFC 9162 §2.1.3.2, step for step."""
    if not isinstance(index, int) or not isinstance(size, int) or isinstance(index, bool) or isinstance(size, bool):
        return "malformed: the index and the size are integers"
    if any(not isinstance(p, (bytes, bytearray)) or len(p) != HASH_SIZE for p in path) \
            or not isinstance(expected_root, (bytes, bytearray)) or len(expected_root) != HASH_SIZE:
        return "malformed: every path element and the root are 32-byte hashes"
    if index < 0 or index >= size:
        return f"index_out_of_range: leaf {index} in a tree of {size}"
    fn, sn = index, size - 1
    r = leaf_hash(leaf)
    for p in path:
        if sn == 0:
            return "path_too_long: the path has more elements than the tree has levels"
        if fn & 1 or fn == sn:
            r = node_hash(p, r)
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            r = node_hash(r, p)
        fn >>= 1
        sn >>= 1
    if sn != 0:
        return "path_too_short: the path ends below the root"
    if r != expected_root:
        return "root_mismatch: the path leads to another root"
    return None
