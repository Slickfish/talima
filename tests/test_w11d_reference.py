"""W11d (run/specs/W11d.md D2, D3) — the reference producer under the specification's own rules.

WHAT IS HELD HERE
    E1  A credential NEXUS issues with EVERY field disclosed is recomputed by the verifier: the disclosed body
        hashes to the record's hash, and it says so. One disclosed value changed (its commitment and the root
        remade, so only the record hash disagrees) is `record_mismatch`.
    E2  NEXUS's chain document (`credentials.chain_document`) is accepted by the verifier's chain check, pinned to
        the instance's identity; it holds hashes and no content.
    E3  Each part of that document altered is refused by name: a link, a block left out, a header member, a
        listed leaf, a signature.
    E4  A chain document needs no private key when every block was signed as it was appended, and then lists no
        keys; a block appended unsigned is signed at issue when the key is given, and refused by name when not.
    E5  The specification's chain vector (`tests/fixtures/w11d/chain.json`) is what NEXUS makes of W11b's golden
        store, byte for byte (SQLite only: the store is SQLite files).
    E6  Appendix A's older forms, as a store that predates W8 really holds them (SQLite only: the committed
        fixture): its bound credential verifies; a binding's block is held to layout 2 and a proof's to layout 2
        or 1; a layout-1 body's index is an integer; a binding that names a record names the disclosed one; and a
        layout-1 block's sealed list is read as lower-case hexadecimal, like every byte string read.
    E7  W8's own proof check stays W8's: on a path in upper case, or with its bytes spaced, the verifier's copy
        gives W8's verdict. The rule about case is the document check's.
"""
from __future__ import annotations

import copy
import hashlib
import json
import shutil

import pytest

from backend.memory import commitments as cm
from backend.memory import credentials
from backend.memory import instance_key as ik
from backend.memory import proofs
from scripts import nexus_verify as nv
from tests._tracked import REPO
from tests.test_w8_bindings import _layout_1_sealed, _pre_run_services, _seal
from tests.test_w8_proofs import QUESTIONS, _sealed

GOLDEN = REPO / "tests" / "fixtures" / "tb_golden"


def _identity(svc) -> dict:
    return ik.identity_document(ik.load_or_create(svc.instance_key_path, chain=svc.archive_chain), svc.archive_chain)


def test_e1_a_full_disclosure_of_a_reference_record_is_recomputed(tmp_path):
    svc, _client, _bearer, ids = _sealed(tmp_path)
    keys = _identity(svc)["keys"]
    partial = credentials.build_credential(svc.lineage, svc.archive_chain, ids[0], key_path=svc.instance_key_path,
                                           disclose=["model_id"])
    every = sorted(partial["commitments"])
    full = json.loads(json.dumps(credentials.build_credential(
        svc.lineage, svc.archive_chain, ids[0], key_path=svc.instance_key_path, disclose=every)))
    verdict = nv.verify_credential(full, keys)
    assert verdict["verified"], verdict["failures"]
    assert any(n.startswith("record_recomputed:") for n in verdict["notes"]), verdict["notes"]
    assert not any(n.startswith("record_recomputed:") for n in nv.verify_credential(
        json.loads(json.dumps(partial)), keys)["notes"]), "a partial disclosure recomputes nothing"

    # one value changed, with its own commitment and the root remade: only the record's hash still disagrees
    forged = copy.deepcopy(full)
    forged["disclosed"]["model_id"]["value"] = "another-model"
    salt = bytes.fromhex(forged["disclosed"]["model_id"]["salt"])
    forged["commitments"]["model_id"] = cm.commitment(salt, "another-model").hex()
    names = [f.split(":")[0] for f in nv.verify_credential(forged, keys)["failures"]]
    assert "record_mismatch" in names and "disclosure_mismatch" not in names, names


def test_e2_the_reference_chain_document_is_accepted_and_holds_hashes_only(tmp_path):
    svc, _client, _bearer, _ids = _sealed(tmp_path)
    document = json.loads(json.dumps(credentials.chain_document(svc.archive_chain, key_path=svc.instance_key_path)))
    verdict = nv.verify_chain(document, _identity(svc)["keys"])
    assert verdict["verified"] and verdict["failures"] == [], verdict
    assert verdict["blocks"] == svc.archive_chain.count() >= 1 and verdict["first_index"] == 0
    assert document["blocks"][0]["leaves"]["lineage"], "the population: the first block seals the turns"
    text = json.dumps(document)
    for question in QUESTIONS:
        assert question not in text and hashlib.sha256(question.encode()).hexdigest() not in text
    unpinned = nv.verify_chain(document)
    assert not unpinned["verified"] and any(f.startswith("key_unpinned") for f in unpinned["failures"])


def test_e3_each_part_of_a_chain_document_altered_is_refused_by_name(tmp_path):
    svc, _client, _bearer, _ids = _sealed(tmp_path)
    svc.archive_chain.append({"note": "a second block"}, "", kind="governance")
    svc.archive_chain.append({"note": "a third block"}, "", kind="governance")
    good = json.loads(json.dumps(credentials.chain_document(svc.archive_chain, key_path=svc.instance_key_path)))
    keys = _identity(svc)["keys"]
    assert nv.verify_chain(good, keys)["verified"] and len(good["blocks"]) >= 3
    flip = lambda h: ("0" if h[0] != "0" else "1") + h[1:]
    alterations = {
        "chain_link_broken": lambda d: d["blocks"][1]["header"].__setitem__(
            "previous_hash", flip(d["blocks"][1]["header"]["previous_hash"])),
        "index_gap": lambda d: d["blocks"].pop(1),
        "block_malformed": lambda d: d["blocks"][2]["header"].pop("snapshot_hash"),
        "block_hash_mismatch": lambda d: d["blocks"][2]["header"].__setitem__("timestamp", 1.0),
        "roots_mismatch": lambda d: d["blocks"][0]["leaves"]["lineage"].__setitem__(
            0, flip(d["blocks"][0]["leaves"]["lineage"][0])),
        "size_mismatch": lambda d: d["blocks"][0]["leaves"]["lineage"].pop(),
        "unsigned": lambda d: d["signatures"].pop(),
        "signature_invalid": lambda d: d["signatures"][0].__setitem__("value", "00" * 64),
        "malformed": lambda d: d.__setitem__("version", 2),
    }
    for name, alter in alterations.items():
        bad = copy.deepcopy(good)
        alter(bad)
        verdict = nv.verify_chain(bad, keys)
        assert not verdict["verified"] and name in [f.split(":")[0] for f in verdict["failures"]], (name, verdict)
    after_zero = copy.deepcopy(good)
    after_zero["blocks"].pop(0)
    after_zero["signatures"] = [s for s in after_zero["signatures"] if s["block_index"] != 0]
    verdict = nv.verify_chain(after_zero, keys)
    assert verdict["verified"] and any(n.startswith("partial_chain:") for n in verdict["notes"]), verdict


def test_e4_a_chain_document_without_the_private_key(tmp_path):
    svc, _client, _bearer, _ids = _sealed(tmp_path)
    keys = _identity(svc)["keys"]
    without = json.loads(json.dumps(credentials.chain_document(svc.archive_chain)))
    assert without["keys"] == [] and {s["signed"] for s in without["signatures"]} == {"seal"}
    assert nv.verify_chain(without, keys)["verified"]
    # a block appended unsigned (its signer failing): signed at issue with the key, refused by name without it
    signer = svc.archive_chain.signer
    svc.archive_chain.signer = lambda index, block_hash: (_ for _ in ()).throw(OSError("planted: no key"))
    try:
        svc.archive_chain.append({"note": "appended unsigned"}, "", kind="governance")
    finally:
        svc.archive_chain.signer = signer
    with pytest.raises(credentials.CredentialRefused, match="unsigned"):
        credentials.chain_document(svc.archive_chain)
    issued = json.loads(json.dumps(credentials.chain_document(svc.archive_chain, key_path=svc.instance_key_path)))
    assert [s["signed"] for s in issued["signatures"]][-1] == "issue"
    assert nv.verify_chain(issued, _identity(svc)["keys"])["verified"]


@pytest.mark.sqlite_only("it opens a copy of W11b's committed SQLite store; under Postgres the chain class opens the "
                         "test database")
def test_e5_the_chain_vector_is_the_golden_stores_chain_byte_for_byte(tmp_path):
    from backend.memory.chain import NexusArchiveChain
    golden = REPO / "tests" / "fixtures" / "tb_golden"
    shutil.copy2(golden / "store" / "archive.db", tmp_path / "archive.db")     # opening a store may write: a copy
    chain = NexusArchiveChain(tmp_path / "archive.db")
    try:
        made = json.dumps(credentials.chain_document(chain), indent=1, sort_keys=True) + "\n"
    finally:
        chain.close()
    vector = (REPO / "tests" / "fixtures" / "w11d" / "chain.json").read_text(encoding="utf-8")
    assert made == vector
    keys = json.loads((golden / "identity.json").read_text(encoding="utf-8"))["keys"]
    verdict = nv.verify_chain(json.loads(vector), keys)
    assert verdict["verified"] and verdict["blocks"] == 2 and verdict["notes"] == [], verdict


@pytest.mark.sqlite_only("it copies the committed pre-run SQLite fixture, as W8's and W9's tests of an older store do")
def test_e6_an_older_stores_bound_credential_is_held_to_what_it_rests_on(tmp_path):
    svc = _pre_run_services(tmp_path)
    lid = sorted(_layout_1_sealed(svc))[0]
    _seal(svc)                                                     # binds it, in a layout-2 block
    key = ik.load_or_create(svc.instance_key_path, chain=svc.archive_chain)
    keys = ik.identity_document(key, svc.archive_chain)["keys"]
    credential = json.loads(json.dumps(credentials.build_credential(
        svc.lineage, svc.archive_chain, lid, key_path=svc.instance_key_path, disclose=["lineage_id"])))
    assert credential["proof"]["block"]["layout"] == 1 and credential["proof"]["binding"]["block"]["layout"] == 2
    assert nv.verify_credential(credential, keys)["verified"]

    def altered(change) -> list:
        bad = copy.deepcopy(credential)
        change(bad)
        return [f.split(":")[0] for f in nv.verify_credential(bad, keys)["failures"]]

    # delta check 2's G1, G7: a binding rests on a layout-2 block; a proof on a layout-2 block or the older layout 1
    for layout in (1, 3, 0):
        assert altered(lambda c: c["proof"]["binding"]["block"].__setitem__("layout", layout)) == \
            ["block_malformed"], layout
    assert altered(lambda c: c["proof"]["binding"]["block"].pop("layout")) == ["block_malformed"]
    assert "block_malformed" in altered(lambda c: c["proof"]["block"].__setitem__("layout", 3))
    # G11: a layout-1 body's index is the document's own structure, and an integer
    body_index = credential["proof"]["block"]["body"]["index"]
    assert "malformed" in altered(lambda c: c["proof"]["block"]["body"].__setitem__("index", float(body_index)))
    # G11: a binding that names its record names the one the credential discloses
    assert altered(lambda c: c["proof"]["binding"].__setitem__("lineage_id", lid)) == []
    assert altered(lambda c: c["proof"]["binding"].__setitem__("lineage_id", "another-record")) == \
        ["lineage_id_mismatch"]

    # G3: the sealed list of a layout-1 block is decoded, so it is lower-case. Another record's hash written in
    # upper case, the block's hash and its signature remade over that text: nothing else is wrong with it.
    def another_in_upper_case(c):
        block = c["proof"]["block"]
        hashes = block["body"]["fingerprint"]["lineage_seal"]["record_hashes"]
        other = next(n for n, h in enumerate(hashes) if h != c["proof"]["record_hash"] and h != h.upper())
        hashes[other] = hashes[other].upper()
        block["block_hash"] = nv._layout_1_hash(block["body"])
        for s in c["signatures"]:
            if s["block_index"] == block["index"]:
                s["block_hash"] = block["block_hash"]
                s["value"] = key.sign(ik.block_statement(block["index"], block["block_hash"])).hex()
    assert altered(another_in_upper_case) == ["malformed"]

    # delta check 3's H3: and written as an object, whose names W8's own check would read as the list
    def as_an_object(upper: bool):
        def change(c):
            block = c["proof"]["block"]
            seal = block["body"]["fingerprint"]["lineage_seal"]
            mine = c["proof"]["record_hash"]
            seal["record_hashes"] = {(h.upper() if upper and h != mine else h): 1 for h in seal["record_hashes"]}
            block["block_hash"] = nv._layout_1_hash(block["body"])
            for s in c["signatures"]:
                if s["block_index"] == block["index"]:
                    s["block_hash"] = block["block_hash"]
                    s["value"] = key.sign(ik.block_statement(block["index"], block["block_hash"])).hex()
        return change
    assert altered(as_an_object(upper=False)) == ["malformed"]
    assert altered(as_an_object(upper=True)) == ["malformed"]

    # H4: the binding is walked like the proof (its integers, its path)
    binding = credential["proof"]["binding"]
    assert binding["path"] and any(x != x.upper() for x in binding["path"])
    for member in ("tree_size", "leaf_index"):
        assert altered(lambda c: c["proof"]["binding"].__setitem__(member, float(binding[member]))) == \
            ["malformed"], member
    assert altered(lambda c: c["proof"]["binding"]["block"].__setitem__("index", float(binding["block"]["index"]))) \
        == ["malformed"]
    assert altered(lambda c: c["proof"]["binding"]["block"].__setitem__("layout", 2.0)) == ["malformed"]
    assert altered(lambda c: c["proof"]["binding"].__setitem__("path", [x.upper() for x in binding["path"]])) == \
        ["malformed"]


def test_e7_w8s_own_proof_check_stays_w8s_on_a_path_in_upper_case():
    spaced = lambda x: " ".join(x[i:i + 2] for i in range(0, len(x), 2))    # noqa: E731
    for name in ("proof.json", "proof_tombstoned.json"):
        proof = json.loads((GOLDEN / name).read_text(encoding="utf-8"))
        assert proof["path"] and nv.verify_proof(proof) is None and proofs.verify_proof(proof) is None, name
        for respell in (str.upper, spaced):
            other = copy.deepcopy(proof)
            other["path"] = [respell(x) for x in other["path"]]
            assert other["path"] != proof["path"]
            assert nv.verify_proof(other) is None and proofs.verify_proof(other) is None, (name, other["path"])
