"""Packet BA — the sentinels for the packet itself.

  - every guard added by fixes 1-6 and 8 has a must-fail companion in the
    same file, enumerated here by AST over the fix files, so a guard whose
    companion is deleted fails this test rather than passing silently;
  - the injection corpus is hashed in source and every must-fail that
    plants an item cites the hash;
  - `done`'s declared payload note carries `imperative_shape`,
    `extraction_failed` and `storage_failed`;
  - the attachment comment says what the code does;
  - fix 7's harness applied its shapes in the harness only while the shape
    was a decision; CHANGE A (18 October) took the decision and shipped A,
    so the loop knows the `prior_answer` block through exactly one module,
    `backend/loop/history.py`, and the delimiter in both its forms is
    gate-refused; the harness's shape functions do what the packet says
    (bound, label, hash; or drop).
"""
from __future__ import annotations

import ast
import hashlib
import sys
from pathlib import Path

from backend.kb.gate import inspect_term
from tests._corpus import CORPUS, CORPUS_SHA256
from tests._tracked import tracked_backend

ROOT = Path(__file__).resolve().parent.parent
TESTS = ROOT / "tests"
sys.path.insert(0, str(ROOT / "scripts"))
import history_shape_harness as hsh  # noqa: E402

# (fix file, guard test, must-fail companion). Names, not descriptions, so
# a rename breaks this table on purpose.
GUARDS = [
    ("test_ba_fix1_summary_role.py",
     "test_no_system_role_message_is_built_from_a_summary",
     "test_the_static_checker_catches_a_summary_placed_in_system_role"),
    ("test_ba_fix1_summary_role.py",
     "test_the_request_carries_exactly_one_prefix_led_system_message",
     "test_a_second_system_message_is_refused_at_assembly"),
    ("test_ba_fix2_request_hash.py",
     "test_the_request_as_sent_hashes_to_the_record",
     "test_one_altered_character_of_history_changes_the_hash_and_fails_derivation"),
    ("test_ba_fix3_prefix_check.py",
     "test_no_rule_compares_a_name_with_itself",
     "test_the_checker_flags_the_old_vacuous_shape"),
    ("test_ba_fix3_prefix_check.py",
     "test_the_true_prefix_passes_every_tier",
     "test_an_altered_prefix_is_reported_at_warn_on_every_tier_and_the_cascade_runs_on"),
    ("test_ba_fix4_source_class.py",
     "test_the_renderer_reads_the_class_from_a_source_attribute_only",
     "test_the_checker_flags_a_class_read_from_anything_else"),
    ("test_ba_fix4_source_class.py",
     "test_every_rendered_line_carries_its_rows_class",
     "test_a_class_spoofed_inside_a_term_never_reaches_the_bracket"),
    ("test_ba_fix5_imperative_shape.py",
     "test_the_rule_is_universal_and_versioned",
     "test_the_corpus_through_the_gate_in_every_evidence_class"),
    ("test_ba_fix6_unknown_not_zero.py",
     "test_the_failure_branches_no_longer_construct_the_zero",
     "test_the_checkers_flag_the_old_shapes"),
    ("test_ba_fix8_pregate_archive.py",
     "test_the_population_is_derived_from_the_gate_not_listed",
     "test_confirm_archives_the_candidates_and_never_the_exempt"),
]


def _functions(path: Path) -> set[str]:
    return {n.name for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(n, ast.FunctionDef)}


def test_every_guard_has_its_must_fail_companion_in_the_same_file():
    files = {f for f, _g, _m in GUARDS}
    assert len(files) == 7, "seven fix files"
    for fname, guard, must_fail in GUARDS:
        fns = _functions(TESTS / fname)
        assert guard in fns, f"{fname} lost its guard {guard}"
        assert must_fail in fns, f"{fname} lost the must-fail for {guard}: {must_fail}"


def test_the_corpus_hash_is_in_source_and_cited_where_items_are_planted():
    recomputed = hashlib.sha256(__import__("json").dumps(
        CORPUS, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    assert recomputed == CORPUS_SHA256
    assert len(CORPUS) == 7 and "control" in CORPUS
    for fname in ("test_ba_fix1_summary_role.py", "test_ba_fix5_imperative_shape.py"):
        src = (TESTS / fname).read_text(encoding="utf-8")
        tree = ast.parse(src)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        assert "CORPUS" in names and "CORPUS_SHA256" in names, f"{fname} plants without citing the hash"


def test_the_declared_payload_note_carries_the_new_vocabulary():
    ref = (ROOT / "docs" / "INTEGRATION_REFERENCE.md").read_text(encoding="utf-8")
    for word in ("imperative_shape", "extraction_failed", "storage_failed", "request_hash"):
        assert word in ref, f"{word} is not in the integration reference"


def test_the_attachment_comment_says_what_the_code_does():
    loop = (ROOT / "backend" / "loop" / "agentic_loop.py").read_text(encoding="utf-8")
    assert "no attachment content is rendered into the prompt" in loop
    assert "get appended to the\n    # prompt context as plain text" not in loop


# ---------------------------------------------------------------------------
# Fix 7's harness: shapes applied in the harness only — until CHANGE A
# ---------------------------------------------------------------------------

def test_the_loop_knows_the_prior_answer_block_through_one_module_and_the_delimiter_is_gate_refused():
    """Until CHANGE A this asserted that NO tracked backend file knew the
    delimiter: fix 7 was a decision, not a build. The decision was taken
    (Fable, 5 October) and A shipped on 18 October, so the sentinel is
    retired deliberately and replaced, not dropped: the delimiter is known
    to exactly one backend module, `backend/loop/history.py`, and the loop
    reaches it only through that module's functions — a second module
    spelling the tag is the drift this now catches. The gate half stands,
    and covers the production grammar's cut form as well."""
    from backend.loop import history as H
    knows = sorted(p.relative_to(ROOT).as_posix() for p in tracked_backend()
                   if 'source="prior_answer"' in p.read_text(encoding="utf-8"))
    assert knows == ["backend/loop/history.py"], knows
    for cls in ("asserted", "external", "extraction"):
        assert inspect_term(hsh.PRIOR_OPEN.format(turn=1, truncated=0), evidence_class=cls) == "markup"
        assert inspect_term(H.wrapper_open(1, truncated=False), evidence_class=cls) == "markup"
        assert inspect_term(H.wrapper_open(1, truncated=True, kept=1795, dropped=612), evidence_class=cls) == "markup"


def test_shape_a_bounds_labels_and_hashes_and_shape_b_drops():
    long_answer = "x" * (hsh.HISTORY_MAX_CHARS + 50) + " " + CORPUS["authority"]
    history = ({"role": "user", "content": "q1"}, {"role": "assistant", "content": long_answer},
               {"role": "user", "content": "q2"}, {"role": "assistant", "content": "short"})
    a = hsh.shape_messages(history, "A")
    assert [m["role"] for m in a] == ["user", "assistant", "user", "assistant"]
    first = a[1]["content"]
    assert first.startswith('<nexus-block source="prior_answer" turn=1 truncated=')
    assert first.endswith(hsh.PRIOR_CLOSE)
    assert CORPUS["authority"] not in first, "the truncation must cut the planted tail"
    assert f"truncated={50 + 1 + len(CORPUS['authority'])}" in first
    # The hash is taken beside the message, not carried on it (BB Part 2):
    # the provider sends message dicts verbatim.
    assert hsh.prior_answer_hashes(history) == {
        1: hashlib.sha256(long_answer.encode("utf-8")).hexdigest(),
        2: hashlib.sha256(b"short").hexdigest()}
    assert all(set(m) == {"role", "content"} for m in a), "only role and content go to the provider"
    assert hsh.shape_messages(a, "A") == a, "wrapping is idempotent across turns"
    b = hsh.shape_messages(history, "B")
    assert b == ({"role": "user", "content": "q2"},)
    assert hsh.shape_messages(history, "control") == history


def test_the_harness_pre_registers_its_bound_and_shapes():
    assert hsh.HISTORY_MAX_CHARS == 600
    assert hsh.SHAPES == ("control", "A", "B")
    assert len(hsh.CONVERSATIONS) == 20 and all(len(c) == 3 for c in hsh.CONVERSATIONS)
