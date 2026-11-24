"""W5 — the reference gateway's prompt entry: search results become prompt lines here, and nowhere else.

WHAT WAS THERE (run/specs/W5.md, S1)
    The loop appended a consented search's results to the dynamic block as
    free text: the engine's name, the title, the whole url and 160 characters
    of the snippet. Nothing judged the text (the ingest gate judges triple
    terms, and these are not triples) and no line carried an evidence class.
    A snippet could tell the model what to do, could close the runtime's own
    `<nexus-block>` tag, and could carry a line that reads exactly like an
    asserted KB line.

WHAT THIS MODULE IS
    The one place a search result's fields are read into prompt text, in
    both positions of `NEXUS_REFERENCE_GATEWAY`:

    off   `legacy_block`: the loop's own lines, moved here unchanged. The
          request is byte-identical to the one before W5 (the pin in
          tests/test_w5_reference_gateway.py).
    on    each result is judged and reduced, and what is admitted is
          labelled on every line:
            - the engine's name is kept only if it is one run of at most
              sixteen lower-case letters and digits, else `unknown`; the url
              is reduced to its host, whose shape is held and whose words
              are not judged;
            - the title is cut at the gate's term length and the snippet at
              160, as before;
            - the title and the snippet are judged by three rules, and a
              result that breaks one is withheld WHOLE, counted by rule and
              declared on the `rag_injected` frame;
            - every admitted line ends with
              `[src=<class> via=web_search:<engine> host=<host>]`.

THE THREE RULES, AND WHY THERE IS NO FOURTH
    `imperative_shape`  the gate's own `imperative_clause` (markers version
                        `ba5.1`), which splits a text into clauses itself;
    `markup`            the gate's own markup pattern;
    `runtime_grammar`   the renderer's own label keys (`src=`, `via=`) and
                        section headers, built from the renderer's constants.

    The first two are IMPORTED from the gate and never copied, and this
    module holds no word list of its own. That is deliberate and it is
    guarded: those two rules were written and measured before this item's
    injection set existed, so nothing in them was tuned on it. It is also
    why the item's adaptive measurement evades the first with one padded
    word, and the documents say so.

    The third is this module's own and was NOT written first. It was
    written after the set's label-carrying seeds were registered, by the
    same author, and wider than the spec first worded it (the keys anywhere
    in a field, where the first wording was `src=` inside square brackets).
    With the first wording the adaptive arm gets every withheld seed of the
    reported list through; as built, all but the two label seeds. The
    figures for this rule are therefore not an independent measurement, and
    docs/REFERENCE_GATEWAY.md says so beside them.

    This gateway reduces what reaches the prompt and labels the rest; the
    report gives the rates.

    The label is provenance. No sentence here or in the prompt asks the
    model to ignore anything.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from backend.kb import source_policy
from backend.kb.gate import MAX_TERM_CHARS, imperative_clause
from backend.kb.gate import _MARKUP as GATE_MARKUP   # the gate's own pattern: imported, never copied
from backend.loop.prompt_render import ASSOC_HEADER, CORTEX_HEADER, KB_HEADER, LEGACY_CORTEX_HEADER

WEB_HEADER = "[WEB SEARCH — top results]"
SNIPPET_CUT = 160            # the cut the loop always made
TITLE_CUT = MAX_TERM_CHARS   # the gate's length for a term

RULE_IMPERATIVE = "imperative_shape"
RULE_MARKUP = "markup"
RULE_RUNTIME = "runtime_grammar"

# An engine's name is written into the label, so it is a channel of its own:
# one run of lower-case letters and digits, no separator, sixteen at most.
_ENGINE_OK = re.compile(r"[a-z0-9]{1,16}")
# A host is labels of letters, digits and inner hyphens, joined by single dots.
_HOST_LABEL = r"[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?"
_HOST_OK = re.compile(rf"{_HOST_LABEL}(?:\.{_HOST_LABEL})*")
_HOST_MAX = 253
# The keys of the label the renderer writes beside a line. A field that holds
# one could pass for a line of higher standing.
_PROVENANCE_KEY = re.compile(r"\b(?:src|via)=")


def _stem(header: str) -> str:
    """`[KB CONTEXT — retrieved …]` -> `[kb context`: what a forged header opens with."""
    return header.split(" — ")[0].lower()


RUNTIME_HEADERS: tuple[str, ...] = tuple(dict.fromkeys(
    _stem(h) for h in (KB_HEADER, ASSOC_HEADER, CORTEX_HEADER, LEGACY_CORTEX_HEADER, WEB_HEADER)))


def legacy_block(results) -> str:
    """The web block as the loop built it before W5, byte for byte."""
    return (
        "\n\n[WEB SEARCH — top results]\n"
        + "\n".join(
            f"  [{r.engine or 'web'}] {r.title}\n"
            f"    {r.url}\n"
            f"    {(r.snippet or '')[:160]}"
            for r in results
        )
    )


def judge(text: str) -> tuple[str, ...]:
    """The rules this text breaks, in a fixed order; empty when it may flow."""
    broken = []
    if imperative_clause(text) is not None:
        broken.append(RULE_IMPERATIVE)
    if GATE_MARKUP.search(text):
        broken.append(RULE_MARKUP)
    low = text.lower()
    if _PROVENANCE_KEY.search(low) or any(stem in low for stem in RUNTIME_HEADERS):
        broken.append(RULE_RUNTIME)
    return tuple(broken)


def engine_of(engine: str) -> str:
    name = (engine or "").strip().lower()
    return name if _ENGINE_OK.fullmatch(name) else "unknown"


def host_of(url: str) -> str:
    try:
        host = (urlsplit(url or "").hostname or "").lower()
    except ValueError:
        host = ""
    return host if len(host) <= _HOST_MAX and _HOST_OK.fullmatch(host) else "unknown"


def _one_line(text: str) -> str:
    """Every run of whitespace, a line break among them, made one space: an
    admitted field is one line, so it cannot open a line with no label."""
    return " ".join(text.split())


@dataclass(frozen=True)
class Admission:
    block: str                 # what is appended to the dynamic block ("" when nothing is admitted)
    results: tuple             # the admitted results, as the `rag_injected` frame carries them
    gateway: dict | None       # the on position's declaration for that frame; None when off


def admit_results(results) -> Admission:
    """Search results to prompt lines, by the position of the switch."""
    results = list(results)
    if not source_policy.gateway_on():
        return Admission(legacy_block(results), tuple(r.to_dict() for r in results), None)

    policy = source_policy.REFERENCE_POLICY["web_search"]
    lines: list[str] = []
    admitted: list[dict] = []
    withheld: list[dict] = []
    by_rule: dict[str, int] = {}
    for rank, r in enumerate(results, 1):
        # Judged twice. As it arrived, because a clause that opens after a
        # line break is still a clause's opening. And as it will be rendered,
        # because the fold makes one space of a tab, a run of spaces or a
        # no-break space, and that one space can complete a section header
        # that the text as it arrived did not hold (the verifier's V1).
        title = (r.title or "")[:TITLE_CUT]
        snippet = (r.snippet or "")[:SNIPPET_CUT]
        broken = tuple(dict.fromkeys(judge(title) + judge(_one_line(title))
                                     + judge(snippet) + judge(_one_line(snippet))))
        if broken:
            withheld.append({"rank": rank, "rules": list(broken)})
            for rule in broken:
                by_rule[rule] = by_rule.get(rule, 0) + 1
                source_policy.count("results_withheld:" + rule)
            continue
        label = (f"[src={policy.declared_class} via={policy.provenance}:{engine_of(r.engine)} "
                 f"host={host_of(r.url)}]")
        lines.append(f"  {_one_line(title)} {label}")
        if snippet.strip():
            lines.append(f"    {_one_line(snippet)} {label}")
        admitted.append(r.to_dict())
    source_policy.count("results_admitted", len(admitted))
    block = ("\n\n" + WEB_HEADER + "\n" + "\n".join(lines)) if lines else ""
    return Admission(block, tuple(admitted), {
        "state": source_policy.ON, "admitted": len(admitted), "withheld": len(withheld),
        "by_rule": dict(sorted(by_rule.items())), "withheld_results": withheld,
    })
