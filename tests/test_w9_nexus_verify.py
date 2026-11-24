"""W9 part 2b — `scripts/nexus_verify.py`, the standalone verifier (run/specs/W9.md D6).

WHAT IS HELD HERE
    V1  Its Ed25519 check is RFC 8032's: the RFC's test vectors 1 to 3 verify, one bit changed in the signature, the
        message or the key fails, and it agrees with `cryptography` on random keys either way.
    V2  Its Merkle tree is W8's: the same root over every tree of 1 to 33 leaves, every leaf's audit path verifies,
        and an altered path is refused with the names `backend/memory/merkle` gives.
    V3  Its commitments are W8's: the same commitment per field and the same root over random bodies.
    V4  Its block statement is the one the instance signs (`instance_key.block_statement`).
    V5  Its proof check is W8's: the same verdict as `backend/memory/proofs.verify_proof` on real proofs and on each
        one altered.
    V6  It stands alone: by AST it imports the standard library and nothing else, and it runs isolated
        (`python -I`) from an empty directory. Companions: an import of a NEXUS module, of a third-party package,
        a dynamic import and a relative one are each named, and none of their names is in the rule.
"""
from __future__ import annotations

import ast
import copy
import json
import os
import random
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from backend.memory import commitments as cm
from backend.memory import instance_key as ik
from backend.memory import merkle, proofs
from scripts import nexus_verify as nv
from tests import _proc
from tests.test_w8_proofs import _sealed

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "nexus_verify.py"
RAW = dict(encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw)
VECTORS = (   # RFC 8032 §7.1, TEST 1 to TEST 3: (secret key, public key, message, signature)
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
)


def _flip(data: bytes, bit: int) -> bytes:
    out = bytearray(data)
    out[bit // 8] ^= 1 << (bit % 8)
    return bytes(out)


def test_v1_the_ed25519_check_is_rfc_8032s_and_agrees_with_cryptography():
    for secret, public, message, signature in VECTORS:
        pk, msg, sig = bytes.fromhex(public), bytes.fromhex(message), bytes.fromhex(signature)
        key = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(secret))
        assert key.public_key().public_bytes(**RAW) == pk and key.sign(msg) == sig, "the vector itself"
        assert nv.ed25519_verify(pk, msg, sig)
        assert not nv.ed25519_verify(pk, msg, _flip(sig, 3)) and not nv.ed25519_verify(pk, msg, _flip(sig, 300))
        assert not nv.ed25519_verify(pk, msg + b"\x00", sig) and not nv.ed25519_verify(_flip(pk, 9), msg, sig)
    rng = random.Random(9)
    for n in range(25):
        key = Ed25519PrivateKey.generate()
        pk, msg = key.public_key().public_bytes(**RAW), rng.randbytes(n * 7)
        sig = key.sign(msg)
        assert nv.ed25519_verify(pk, msg, sig)
        assert not nv.ed25519_verify(pk, msg, _flip(sig, rng.randrange(512)))
    assert not nv.ed25519_verify(b"\x00" * 31, b"", bytes(64)) and not nv.ed25519_verify(pk, b"", sig[:63])


def test_v2_the_merkle_tree_is_w8s():
    rng = random.Random(2)
    for size in range(1, 34):
        leaves = [rng.randbytes(rng.randrange(1, 70)) for _ in range(size)]
        root = merkle.root(leaves)
        assert nv.merkle_root(leaves) == root, size
        for i in range(size):
            path = merkle.audit_path(leaves, i)
            assert nv.verify_inclusion(leaves[i], i, size, path, root) is None
            if path:
                bad = [_flip(path[0], 0)] + path[1:]
                assert nv.verify_inclusion(leaves[i], i, size, bad, root) == merkle.verify_inclusion(
                    leaves[i], i, size, bad, root)
            for args in ((leaves[i], size, size, path, root), (leaves[i], i, size, path + [root], root),
                         (leaves[i], i, size, path[:-1], root), (leaves[i], True, size, path, root)):
                assert nv.verify_inclusion(*args) == merkle.verify_inclusion(*args), (size, i)
    assert nv.merkle_root([]) == merkle.root([])


def test_v3_the_commitments_are_w8s():
    rng = random.Random(3)
    for _ in range(20):
        body = {f"f{n}": rng.choice([rng.random(), rng.randrange(10 ** 9), "t" * rng.randrange(9), None,
                                     [rng.random(), {"k": "v"}]]) for n in range(rng.randrange(1, 12))}
        made = cm.commit(body)
        assert {f: nv.commitment(made.salts[f], body[f]) for f in body} == made.commitments
        assert nv.commitment_root(made.commitments) == made.root


def test_v4_the_statement_is_the_one_the_instance_signs():
    for index, h in ((0, "00" * 32), (7, "ab" * 32), (2 ** 40 + 3, "0f" * 32)):
        assert nv.STATEMENT_PREFIX + index.to_bytes(8, "big") + bytes.fromhex(h) == ik.block_statement(index, h)


def test_v5_the_proof_check_is_w8s_on_real_proofs_and_altered_ones(tmp_path):
    svc, _client, _bearer, ids = _sealed(tmp_path)
    for lid in ids:
        proof = json.loads(json.dumps(proofs.build_proof(svc.lineage, svc.archive_chain, lid)))
        assert nv.verify_proof(proof) is None and proofs.verify_proof(proof) is None
        alterations = [
            lambda p: p.__setitem__("leaf", p["leaf"][:-1] + ("0" if p["leaf"][-1] != "0" else "1")),
            lambda p: p.__setitem__("leaf_index", p["leaf_index"] + 1),
            lambda p: p.__setitem__("tree_size", p["tree_size"] + 1),
            lambda p: p["block"]["header"].__setitem__("timestamp", p["block"]["header"]["timestamp"] + 1),
            lambda p: p["block"].__setitem__("block_hash", "00" * 32),
            lambda p: p.__setitem__("record_hash", p["record_hash"][:10]),
            lambda p: p.__setitem__("path", ["11" * 32] + p["path"]),
            lambda p: p.__setitem__("state", "unsealed"),
            lambda p: p.pop("block"),
        ]
        for alter in alterations:
            bad = copy.deepcopy(proof)
            alter(bad)
            assert nv.verify_proof(bad) == proofs.verify_proof(bad) and nv.verify_proof(bad) is not None


STDLIB = frozenset(sys.stdlib_module_names)


def standalone_problems(source: str) -> list[str]:
    """Why a verifier's source is not standalone: every import must name a
    standard-library module, absolutely, by a literal statement."""
    problems = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            problems += [f"imports {a.name}" for a in node.names if a.name.split(".")[0] not in STDLIB]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                problems.append(f"a relative import from {'.' * node.level}{node.module or ''}")
            elif (node.module or "").split(".")[0] not in STDLIB:
                problems.append(f"imports from {node.module}")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name in ("import_module", "__import__"):
                problems.append(f"a dynamic import at line {node.lineno}")
    return problems


def test_v6_the_verifier_stands_alone(tmp_path):
    source = SCRIPT.read_text(encoding="utf-8")
    assert standalone_problems(source) == []
    empty = tmp_path / "empty"
    empty.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
    proc = _proc.run([sys.executable, "-I", str(SCRIPT), "--help"], cwd=empty, env=env, timeout=60,
                     capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "nexus-verify" in proc.stdout, proc.stdout + proc.stderr
    assert list(empty.iterdir()) == []


def test_v6_companions_an_import_that_is_not_the_standard_librarys_is_named():
    base = SCRIPT.read_text(encoding="utf-8")
    plants = {
        "a NEXUS module": "from backend.memory import merkle\n",
        "a third-party package": "import requests\n",
        "a dynamic import": "importlib.import_module('backend.memory.merkle')\n",
        "a relative import": "from . import helpers\n",
    }
    rule = ast.get_source_segment(Path(__file__).read_text(encoding="utf-8"), next(
        n for n in ast.parse(Path(__file__).read_text(encoding="utf-8")).body
        if isinstance(n, ast.FunctionDef) and n.name == "standalone_problems"))
    for label, line in plants.items():
        assert standalone_problems(base + line), label
    for word in ("backend", "requests", "helpers"):
        assert word not in rule, f"the rule names {word}"
