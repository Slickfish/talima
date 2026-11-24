"""W8 part 1 — the Merkle tree of RFC 6962, its audit paths and their verification (run/specs/W8.md, D1, exit 6).

WHAT IS HELD HERE
    M1  The root of every tree of one to eight leaves equals the root of an independent construction written in
        this file from RFC 6962 §2.1's definition (recursion on the largest power of two below n), and the eight
        published roots of the RFC 6962 test leaves the Certificate Transparency projects use. Two checks the module
        did not produce: this file's recursion, and numbers published elsewhere.
    M2  Every audit path of every tree of one to seventeen leaves verifies with `verify_inclusion`, from the leaf,
        the index, the size, the path and the root alone.
    M3  A proof altered in any one way is refused by name: a path element changed, one dropped or added, the index
        changed, a size whose tree has another shape along the path, the root changed, another leaf.
    M3b What a path does not prove: the tree's size. A size whose tree has the same shape along the leaf's path
        verifies against the same root (RFC 9162 authenticates the size through the signed tree head, not the
        path), so whoever presents a proof must bind the size to something sealed: W8's block header carries the
        seal's count, and the proof route takes the size from it.
    M4  The domain separation is what stops an inner node being presented as a leaf: with it, leaves made of two
        child hashes concatenated do not reproduce the root; planted away (no 0x00 or 0x01 prefixes), they do,
        which is the second preimage the prefixes exist to stop.
"""
from __future__ import annotations

import hashlib

import pytest

from backend.memory import merkle

# RFC 6962's test leaves, as the Certificate Transparency projects publish them, and the root of the tree of the
# first n of them for n = 1..8.
TEST_LEAVES = [bytes.fromhex(h) for h in (
    "", "00", "10", "2021", "3031", "40414243", "5051525354555657", "606162636465666768696a6b6c6d6e6f")]
PUBLISHED_ROOTS = [
    "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
    "fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125",
    "aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77",
    "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
    "4e3bbb1f7b478dcfe71fb631631519a3bca12c9aefca1612bfce4c13a86264d4",
    "76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef",
    "ddb89be403809e325750d3d263cd78929c2942b7942a34b77e122c9594a74c8c",
    "5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328",
]


def _independent_root(leaves: list[bytes]) -> bytes:
    """RFC 6962 §2.1, written from the definition and nowhere else: MTH({}) = SHA-256(); MTH({d}) =
    SHA-256(0x00 || d); otherwise split at k, the largest power of two smaller than n."""
    if not leaves:
        return hashlib.sha256(b"").digest()
    if len(leaves) == 1:
        return hashlib.sha256(b"\x00" + leaves[0]).digest()
    k = 1
    while k * 2 < len(leaves):
        k *= 2
    return hashlib.sha256(b"\x01" + _independent_root(leaves[:k]) + _independent_root(leaves[k:])).digest()


def _leaves(n: int) -> list[bytes]:
    return [f"leaf-{i}".encode() for i in range(n)]


def test_m1_roots_equal_an_independent_construction_and_the_published_roots():
    assert merkle.root([]) == hashlib.sha256(b"").digest()
    for n in range(1, len(TEST_LEAVES) + 1):
        got = merkle.root(TEST_LEAVES[:n])
        assert got == _independent_root(TEST_LEAVES[:n]), n
        assert got.hex() == PUBLISHED_ROOTS[n - 1], n
    for n in range(1, 40):
        assert merkle.root(_leaves(n)) == _independent_root(_leaves(n)), n


def test_m2_every_path_of_every_small_tree_verifies_from_the_proof_alone():
    for n in range(1, 18):
        leaves = _leaves(n)
        root = merkle.root(leaves)
        for m in range(n):
            path = merkle.audit_path(leaves, m)
            assert merkle.verify_inclusion(leaves[m], m, n, path, root) is None, (n, m)


def test_m3_a_proof_altered_in_any_one_way_is_refused_by_name():
    leaves = _leaves(11)
    root, m = merkle.root(leaves), 6
    path = merkle.audit_path(leaves, m)
    assert path and merkle.verify_inclusion(leaves[m], m, 11, path, root) is None
    flipped = bytes([path[1][0] ^ 1]) + path[1][1:]
    cases = {
        "a path element changed": (leaves[m], m, 11, [path[0], flipped, *path[2:]], root),
        "a path element dropped": (leaves[m], m, 11, path[:-1], root),
        "a path element added": (leaves[m], m, 11, [*path, path[0]], root),
        "the index changed": (leaves[m], m + 1, 11, path, root),
        "a size whose tree has another shape": (leaves[m], m, 7, path, root),
        "the index past the size": (leaves[m], 11, 11, path, root),
        "the root changed": (leaves[m], m, 11, path, hashlib.sha256(b"another").digest()),
        "another leaf": (leaves[m + 1], m, 11, path, root),
    }
    for label, args in cases.items():
        refusal = merkle.verify_inclusion(*args)
        assert isinstance(refusal, str) and refusal, label
        assert refusal.split(":")[0] in merkle.REFUSALS, (label, refusal)


def test_m3b_a_path_does_not_prove_the_size_and_the_test_says_so():
    leaves = _leaves(11)
    root, m = merkle.root(leaves), 6
    path = merkle.audit_path(leaves, m)
    assert merkle.verify_inclusion(leaves[m], m, 12, path, root) is None, "the limit this pins has moved"
    assert merkle.root(_leaves(12)) != root, "a tree of twelve has another root: the size is bound by the root's seal"


def test_m4_without_the_prefixes_an_inner_node_passes_for_a_leaf(monkeypatch):
    leaves = _leaves(4)

    def forged() -> list[bytes]:
        h = [merkle.leaf_hash(x) for x in leaves]
        return [h[0] + h[1], h[2] + h[3]]      # each "leaf" is an inner node's two children

    # with the prefixes: a leaf is hashed under 0x00 and a node under 0x01, so the forgery has another root
    assert merkle.root(forged()) != merkle.root(leaves)
    # planted away: the forged two-leaf tree has the four-leaf tree's root
    monkeypatch.setattr(merkle, "LEAF_PREFIX", b"")
    monkeypatch.setattr(merkle, "NODE_PREFIX", b"")
    assert merkle.root(forged()) == merkle.root(leaves)


def test_m3_a_path_cut_short_against_an_inner_node_is_refused():
    """The path stops at a subtree whose root is presented as the tree's: every hash checks out, and only the
    path's length against the size says the leaf was not shown to be in a tree of that size."""
    leaves = _leaves(4)
    path = merkle.audit_path(leaves, 0)
    subtree = merkle.root(leaves[:2])
    assert merkle.verify_inclusion(leaves[0], 0, 4, path[:1], subtree).startswith("path_too_short")


@pytest.mark.parametrize("bad", [b"", b"\x00" * 31, b"\x00" * 33])
def test_m3_a_path_element_that_is_not_a_hash_is_refused(bad):
    leaves = _leaves(3)
    path = merkle.audit_path(leaves, 0)
    assert merkle.verify_inclusion(leaves[0], 0, 3, [bad, *path[1:]], merkle.root(leaves)).startswith("malformed")
