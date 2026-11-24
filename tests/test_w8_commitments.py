"""W8 part 3 — salted field commitments (run/specs/W8.md, D4; exit 3).

WHAT IS HELD HERE
    K1  Every field of a record's hashed body gets its own 32-byte salt and the commitment
        SHA-256(salt || canonical(value)); the commitment root is RFC 6962's root over `name || 0x00 || C`, in
        field-name order. Both are recomputed here by a construction written in this file.
    K2  An opening (a value and its salt) is accepted for the field it was committed under and refused by name for
        a wrong salt, a wrong value, another field's commitment, and a field that has none.
    K3  A field the body does not carry has no commitment: absent stays absent, as the record's hash rule has it.
    K4  The same values under fresh salts commit differently (nothing undisclosed can be matched against another
        record's commitments), and under the same salts identically.
    K5  A short value (a yes, a number) is not recoverable from its commitment by trying candidates, as it is from
        the record's unsalted hash of it: the commitment is not that hash.
"""
from __future__ import annotations

import hashlib
import json

from backend.memory import commitments as cm
from backend.memory import merkle

BODY = {"lineage_id": "8f3c9f5e-1111-4222-8333-944455556666", "ts": 1763300000.25,
        "query_hash": hashlib.sha256(b"what does sleep improve?").hexdigest(),
        "injected": [{"belief_id": 7, "weight_at_injection": 0.61, "cited": True}],
        "model_id": "stub:primary", "answer_hash": hashlib.sha256(b"yes").hexdigest()}


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def test_k1_each_field_has_its_salt_and_commitment_and_the_root_is_rfc_6962s():
    made = cm.commit(BODY)
    assert set(made.salts) == set(made.commitments) == set(BODY)
    assert all(isinstance(s, bytes) and len(s) == cm.SALT_BYTES == 32 for s in made.salts.values())
    for name, value in BODY.items():
        assert made.commitments[name] == hashlib.sha256(made.salts[name] + _canonical(value)).digest(), name
    leaves = [name.encode() + b"\x00" + made.commitments[name] for name in sorted(BODY)]
    assert made.root == merkle.root(leaves)


def test_k2_an_opening_is_accepted_for_its_field_and_refused_by_name_otherwise():
    made = cm.commit(BODY)
    salt = made.salts["answer_hash"]
    assert cm.open_field("answer_hash", BODY["answer_hash"], salt, made.commitments) is None
    refusals = {
        "a wrong salt": cm.open_field("answer_hash", BODY["answer_hash"], bytes(32), made.commitments),
        "a wrong value": cm.open_field("answer_hash", hashlib.sha256(b"no").hexdigest(), salt, made.commitments),
        "another field's commitment": cm.open_field("query_hash", BODY["answer_hash"], salt, made.commitments),
        "a field with none": cm.open_field("scope", "default", salt, made.commitments),
        "a salt that is not 32 bytes": cm.open_field("answer_hash", BODY["answer_hash"], salt[:16], made.commitments),
    }
    for label, refusal in refusals.items():
        assert isinstance(refusal, str) and refusal.split(":")[0] in cm.REFUSALS, (label, refusal)


def test_k3_a_field_the_body_does_not_carry_has_no_commitment():
    made = cm.commit({k: v for k, v in BODY.items() if k != "answer_hash"})
    assert "answer_hash" not in made.commitments and "answer_hash" not in made.salts
    assert cm.open_field("answer_hash", BODY["answer_hash"], bytes(32), made.commitments).startswith("no_commitment")


def test_k4_fresh_salts_hide_and_the_same_salts_bind():
    a, b = cm.commit(BODY), cm.commit(BODY)
    assert all(a.commitments[k] != b.commitments[k] for k in BODY) and a.root != b.root
    again = cm.commit(BODY, salts=a.salts)
    assert again.commitments == a.commitments and again.root == a.root


def test_k5_a_short_value_is_not_recovered_from_its_commitment_by_trying_candidates():
    made = cm.commit({"answer": "yes"})
    candidates = ["yes", "no", "maybe", "0", "1", "true", "false"]
    unsalted = {hashlib.sha256(_canonical(c)).digest() for c in candidates}
    assert made.commitments["answer"] not in unsalted
    # the record's own field is an unsalted hash, and a candidate finds it at once: the reason for the salt
    assert hashlib.sha256(b"yes").hexdigest() in {hashlib.sha256(c.encode()).hexdigest() for c in candidates}


def test_k6_values_the_canonical_encoding_cannot_carry_are_refused_and_a_short_salt_by_name():
    """W8's verifier: NaN and the infinities have no canonical JSON, so they are refused, never committed as some
    spelling (C1); a salt that is not 32 bytes is refused as malformed, not as a mismatch (C5)."""
    import math
    import pytest
    for value in (math.nan, math.inf, -math.inf, {"x": [math.nan]}):
        with pytest.raises(ValueError):
            cm.canonical(value)
    made = cm.commit(BODY)
    refusal = cm.open_field("answer_hash", BODY["answer_hash"], made.salts["answer_hash"][:16], made.commitments)
    assert refusal.split(":")[0] == "malformed", refusal
