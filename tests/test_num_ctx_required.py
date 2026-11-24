"""AV — every caller states its context; the ceiling only refuses.

THE DEFECT, observed on the operator's card: `ollama ps` showed phi4-mini
at 8192 (4.1 GB) beside the answering model (8.8 GB) — more than the 12 GB
card — so every turn evicted one to load the other. Extraction requested
8192 as a literal (extraction.py) and the answering path inherited 8192
from `stream_chat`'s default, which was Law 5's ceiling. Neither was a
sized choice: the ceiling's job is to refuse what is too large, and it had
been supplying the value instead.

THE FIX: `num_ctx` is REQUIRED on `stream_chat` (the ABC and both
providers); the answering path states compaction's TOKEN_BUDGET; extraction
derives its size from its input cap through the project's token estimator
(4096 at the shipped cap), constant per configuration so the runner never
reloads on a longer answer.

THE SENTINEL is derived from the call sites, not listed: every call of a
reply chokepoint (`stream_chat`, `stream_pieces`, `reply_stream`,
`collect_reply`, `ReplyStream`) in tracked backend and script files must
pass `num_ctx` AS A KEYWORD — the three sites that pass it positionally
are answered for by name and checked for passing the context they were
themselves given — and every other reference to a chokepoint (a bound
alias, a `functools.partial`, anything that is not the function of a
call or a type annotation) is refused, because it is a call this guard
cannot read. So is a chokepoint's NAME AS A STRING, wherever it stands
(the review's R4: `getattr(p, "stream_chat")` was read, and
`attrgetter("stream_chat")`, `methodcaller("stream_chat", ...)`,
`p.__getattribute__("stream_chat")`, `vars(type(p))["stream_chat"]`, a
name bound first and passed to `getattr`, and `"stream_" + "chat"` were
not): a constant, a `+` of constants or an f-string's literal parts are
folded to one text, and the text is a reference when it is a chokepoint
name alone or the last member of a dotted or colon path (the shapes a
program resolves an attribute by), unless answered for by (file, the
expression that holds it) — two today, the getattr `ReplyStream` asks the
provider with and a script's report label. Outside the rule by its
shape, recorded: a text computed at run time (`"STREAM_CHAT".lower()`, a
`%` format, a `join`) and a string that merely contains the name (the
same script's `"stream_chat (Ruling 2)"` label and help text). The
second verifier's F5: the first version read `num_ctx` at a position, so
an unbound `Cls.stream_chat(p, model, messages)` counted its receiver as
the context, and an alias was not a site at all. No function, method or
lambda there that takes a `num_ctx` parameter, whatever its name, may
give it a default, and no class body may hold a `num_ctx` field with a
value — a dataclass field is the generated `__init__`'s default (N4).
Outside the rule by its shape, recorded: a default read through another
name, `kw.get("num_ctx", 8192)` or a parameter `ctx=8192` forwarded as
`num_ctx`. W1 widened both rules: the retired shim's `stream_chat` sat
outside `providers/`, and W1's verifier (F7) planted the ceiling as a
default on `collect_reply`, the helper W1 added, with the whole suite
green.

THE ACCIDENT, recorded: the operator's earlier both-resident state
(phi4-mini at 3.3 GB, context 4096, beside qwen) was a runner loaded by
something OUTSIDE NEXUS at Ollama's default — the CLI or a bare probe —
not a configuration. Nothing here restores it; the sized request produces
the same footprint by design.
"""
from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path

import pytest

from backend.cortex import extraction
from backend.cortex.extraction import extraction_num_ctx, extract_triples
from backend.loop.compaction import TOKEN_BUDGET
from backend.providers.base import num_ctx_for
from backend.providers.ollama import NUM_CTX, OllamaProvider
from tests._tracked import tracked_backend, tracked_scripts

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Derived over the call sites and the definitions
# ---------------------------------------------------------------------------

# The reply chokepoints, by name. `num_ctx` is a KEYWORD at every call of
# one, except at the sites answered for below, which pass it as the third
# positional argument and are checked for exactly that. The first version
# read `num_ctx` at position 2 of any `stream_chat`/`stream_pieces` call, so
# an unbound `Cls.stream_chat(p, model, messages)` counted its explicit
# receiver as the context (the second verifier's F5).
CHOKEPOINTS = {"stream_chat", "stream_pieces", "reply_stream", "collect_reply", "ReplyStream"}

# (file, the called function's source text) -> why it passes num_ctx
# positionally. Checked, not exempt: the third positional argument must be
# the context the caller itself received (`num_ctx` or `self.num_ctx`), and
# an entry no positional call matches (the site gone, or rewritten as a
# keyword) is a failure.
ANSWERED_POSITIONAL = {
    ("backend/providers/ollama.py", "self.stream_pieces"):
        "stream_chat forwards its own num_ctx to the separating path",
    ("backend/providers/lmstudio.py", "self.stream_pieces"):
        "the same, for LM Studio",
    ("backend/providers/openai_compat.py", "self.stream_pieces"):
        "the same, for the OpenAI-compatible endpoint (W3 part 2)",
    ("backend/providers/anthropic.py", "self.stream_pieces"):
        "the same, for the Anthropic Messages API (W3 part 2)",
    ("backend/providers/base.py", "native"):
        "ReplyStream._pieces calls the provider's stream_pieces through the name the getattr below binds",
}
# (file, the source text of the expression that holds the string) -> why a
# chokepoint's name stands there as a string. Checked, not exempt: an entry
# no string matches is a failure; and the name a `getattr` on an answered
# entry binds is read as a call of that chokepoint wherever the file calls
# it (the review's R4 widened this from `getattr` to every string).
ANSWERED_STRINGS = {
    ("backend/providers/base.py", "getattr(self.provider, 'stream_pieces', None)"):
        "ReplyStream._pieces asks whether the provider separates natively; None falls back to stream_chat",
    ("scripts/measure_vocabulary_compliance.py",
     "'collect_reply' if through_chokepoint else 'stream_chat (Ruling 2)'"):
        "the arm's `read_through` label on its report: which chokepoint the run was read through, printed, not read",
}
# Where the companions plant: a script that does not exist.
PLANTED_PATH = "scripts/ask_once.py"


def _folded(node) -> str | None:
    """The text a string expression spells with its literal parts: a
    constant, a `+` of constants, or an f-string's literal parts (the shim
    guard's reader). None for anything else, so a text computed at run time
    is outside the rule by shape."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _folded(node.left), _folded(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant))
    return None


def _name_as_string(node) -> str | None:
    """The chokepoint a string expression spells — alone, or as the last
    member of a dotted or colon path (`"provider.stream_chat"`, the form
    `attrgetter` and `pkgutil.resolve_name` take) — or None."""
    text = _folded(node)
    if text is None:
        return None
    m = re.fullmatch(r"(?:[\w.]*[.:])?(\w+)", text)
    return m.group(1) if m and m.group(1) in CHOKEPOINTS else None


def _sources() -> dict[str, str]:
    return {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
            for p in list(tracked_backend()) + list(tracked_scripts())}


def _annotation_nodes(tree) -> set[int]:
    """ids of every node inside a type annotation: a chokepoint's name there
    (`-> ReplyStream`) is a type, not a site."""
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = n.args.posonlyargs + n.args.args + n.args.kwonlyargs
            args += [a for a in (n.args.vararg, n.args.kwarg) if a is not None]
            anns = [n.returns] + [a.annotation for a in args]
        elif isinstance(n, ast.AnnAssign):
            anns = [n.annotation]
        else:
            continue
        for a in anns:
            if a is not None:
                out |= {id(x) for x in ast.walk(a)}
    return out


def _getattr_of_chokepoint(node) -> str | None:
    """The chokepoint a `getattr(x, "<name>", ...)` call reads, or None."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr" \
            and len(node.args) >= 2:
        return _name_as_string(node.args[1])
    return None


def _scan(sources: dict[str, str]) -> tuple[list[tuple[str, int, str, bool]], list[str], set, set]:
    """One pass over the sources: every chokepoint call as (file, line, name,
    passes num_ctx), every reference this cannot read, and the keys the
    answered sites were met under."""
    calls, references, positional_seen, strings_seen = [], [], set(), set()
    for path, src in sorted(sources.items()):
        tree = ast.parse(src)
        annotations = _annotation_nodes(tree)
        call_funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        aliases: dict[str, str] = {}
        skip: set[int] = set()
        for n in ast.walk(tree):
            # A chokepoint's name as a string, read once at its outermost
            # fold (the constants inside a reported `+` or f-string are not
            # reported again), keyed on the expression that holds it.
            if id(n) in skip or id(n) in annotations:
                continue                    # a quoted annotation (`-> 'ReplyStream'`) is a type, not a site
            cp = _name_as_string(n)
            if cp is None:
                continue
            skip |= {id(d) for d in ast.walk(n) if d is not n}
            holder = parents.get(n, n)
            key = (path, ast.unparse(holder))
            if key in ANSWERED_STRINGS:
                strings_seen.add(key)
            else:
                references.append(f"{path}:{n.lineno}: {cp} read by its name as a string: {ast.unparse(holder)[:80]}")
        for n in ast.walk(tree):
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) \
                    and _getattr_of_chokepoint(n.value) and (path, ast.unparse(n.value)) in strings_seen:
                aliases[n.targets[0].id] = _getattr_of_chokepoint(n.value)
        for n in ast.walk(tree):
            name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else None
            if name in CHOKEPOINTS and id(n) not in call_funcs and id(n) not in annotations:
                references.append(f"{path}:{n.lineno}: a reference to {name} that is not a call: {ast.unparse(n)}")
            if not isinstance(n, ast.Call):
                continue
            called = n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", None)
            if called in aliases:
                called = f"{aliases[called]} via {n.func.id}"
            elif called not in CHOKEPOINTS:
                continue
            key = (path, ast.unparse(n.func))
            keyword = any(kw.arg == "num_ctx" for kw in n.keywords)
            if key in ANSWERED_POSITIONAL and not keyword:
                positional_seen.add(key)
            passes = keyword or (
                key in ANSWERED_POSITIONAL and len(n.args) > 2
                and ast.unparse(n.args[2]) in ("num_ctx", "self.num_ctx"))
            calls.append((path, n.lineno, called, passes))
    return calls, references, positional_seen, strings_seen


def chokepoint_calls(sources: dict[str, str]) -> list[tuple[str, int, str, bool]]:
    """(file, line, name, passes num_ctx) for every call of a chokepoint,
    including a call through a name an answered getattr bound."""
    return _scan(sources)[0]


def context_problems(sources: dict[str, str]) -> tuple[list[tuple[str, int, str, bool]], list[str]]:
    """(the calls, everything the guard refuses): a call that omits the
    `num_ctx` keyword (an unbound call, a positional context at a site
    nobody answered for), a reference this cannot read — a bound alias
    (`f = provider.stream_chat`), a `functools.partial` argument, a
    chokepoint's name as a string (`getattr`, `attrgetter`, a subscript of
    `vars(...)`, a bound name) nobody answered for — and an answered site
    that is gone."""
    calls, references, positional_seen, strings_seen = _scan(sources)
    problems = [f"a chokepoint called without num_ctx at {f}:{ln} ({c})" for f, ln, c, ok in calls if not ok]
    problems += references
    problems += [f"answered but absent: {k}" for k in sorted(set(ANSWERED_POSITIONAL) - positional_seen)]
    problems += [f"answered but absent: {k}" for k in sorted(set(ANSWERED_STRINGS) - strings_seen)]
    return calls, problems


def test_every_call_site_states_its_context():
    calls, problems = context_problems(_sources())
    print(f"\nAV chokepoint calls: {[(f, ln, c) for f, ln, c, _ok in calls]}")
    assert {c for _f, _ln, c, _ok in calls} >= {"stream_chat", "stream_pieces", "reply_stream", "collect_reply"}, \
        "derivation found too few kinds of call site"
    assert any(c.startswith("stream_pieces via ") for _f, _ln, c, _ok in calls), \
        "the answered getattr's alias is read as a call"
    assert not problems, "the context guard refuses:\n  " + "\n  ".join(problems)


def default_num_ctx_offenders(sources: dict[str, str]) -> list[tuple[str, int]]:
    """Every function, method or lambda, whatever its name, whose `num_ctx`
    parameter has a default, and every class-body field named `num_ctx`
    with a value (a dataclass field is the generated `__init__`'s default;
    a class attribute is every instance's). W1 widened the population
    twice: from `backend/providers/` to every tracked backend and script
    file (the retired shim's `stream_chat` defaulted to the Law 5 ceiling
    outside `providers/`), and from two names to any (F7: a default on
    `collect_reply`, `reply_stream` or `ReplyStream` passed the suite);
    the second verifier (N4) added the lambda and the field. Outside the
    rule by its shape: a default read through another name,
    `kw.get("num_ctx", 8192)` or a parameter `ctx=8192` forwarded as
    `num_ctx` — the parameter is not named `num_ctx`, so this cannot see it."""
    offenders = []
    for name, src in sources.items():
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                positional = node.args.posonlyargs + node.args.args
                defaults = ([None] * (len(positional) - len(node.args.defaults))
                            + list(node.args.defaults))
                pairs = list(zip(positional, defaults)) + list(zip(node.args.kwonlyargs, node.args.kw_defaults))
                for a, d in pairs:
                    if a.arg == "num_ctx" and d is not None:
                        offenders.append((name, node.lineno))
            elif isinstance(node, ast.ClassDef):
                for stmt in node.body:
                    target = stmt.target if isinstance(stmt, ast.AnnAssign) else (
                        stmt.targets[0] if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 else None)
                    if isinstance(target, ast.Name) and target.id == "num_ctx" and stmt.value is not None:
                        offenders.append((name, stmt.lineno))
    return offenders


def test_no_provider_supplies_a_default():
    sources = _sources()
    takes = sorted({(f, n.name) for f, src in sources.items() for n in ast.walk(ast.parse(src))
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and "num_ctx" in [a.arg for a in n.args.posonlyargs + n.args.args + n.args.kwonlyargs]})
    print(f"\nAV functions taking num_ctx: {takes}")
    assert len(takes) >= 10, "non-vacuity: the derivation found the functions that take num_ctx"
    offenders = default_num_ctx_offenders(sources)
    assert not offenders, f"a function supplies a default num_ctx at {offenders}"


def test_a_non_provider_module_supplying_the_ceiling_is_named():
    """Companion: the shim's shape, planted outside providers/ (the place the
    pre-W1 guard skipped)."""
    planted = ("from backend.providers.ollama import NUM_CTX\n"
               "async def stream_chat(model, messages, *, num_ctx=NUM_CTX, **_):\n"
               "    yield ''\n")
    assert default_num_ctx_offenders({"backend/legacy_client.py": planted}) == [
        ("backend/legacy_client.py", 2)]


# F7's companions: each plants the ceiling where the first widening could
# not see it, and the consequence asserted is the guard's own test failing.
PLANTED_DEFAULTS = {
    "the collect_reply helper": ("backend/providers/base.py", (
        "async def collect_reply(provider, model, messages, *, num_ctx: int = 8192):\n    return None\n")),
    "the reply_stream chokepoint": ("backend/providers/base.py", (
        "def reply_stream(provider, model, messages, *, num_ctx=8192):\n    return None\n")),
    "a method on a new class": ("backend/providers/base.py", (
        "class Stream:\n    def __init__(self, provider, model, messages, num_ctx=4096):\n        pass\n")),
    "a script's own helper": ("scripts/ask_once.py", (
        "def ask(provider, messages, num_ctx=2048):\n    return provider\n")),
    # The second verifier's N4: a lambda's default, a dataclass field and a
    # class attribute, none of them a def.
    "a lambda's default": ("scripts/ask_once.py", (
        "ask = lambda provider, model, messages, num_ctx=8192: provider.stream_chat(model, messages, num_ctx=num_ctx)\n")),
    "a dataclass field": ("backend/providers/base.py", (
        "from dataclasses import dataclass\n@dataclass\nclass Cfg:\n    num_ctx: int = 8192\n")),
    "a class attribute": ("backend/providers/base.py", (
        "class Settings:\n    num_ctx = 4096\n")),
}


@pytest.mark.parametrize("name", sorted(PLANTED_DEFAULTS))
def test_a_default_on_any_name_is_named(name):
    path, code = PLANTED_DEFAULTS[name]
    sources = _sources()
    sources[path] = sources.get(path, "") + "\n\n" + code
    assert any(f == path for f, _ln in default_num_ctx_offenders(sources)), name


PLANTED_CALLS = {
    "collect_reply without num_ctx": "await collect_reply(provider, model, messages)",
    "reply_stream without num_ctx": "rs = reply_stream(provider, model, messages)",
    "stream_pieces with two arguments": "provider.stream_pieces(model, messages)",
    # The second verifier's F5: the unbound call, whose explicit receiver the
    # first rule counted as the context; and a positional context at a site
    # nobody answered for, which the keyword rule refuses.
    "an unbound call through the class": (
        "async for t in OllamaProvider.stream_chat(provider, model, messages):\n        pass"),
    "a positional context at an unanswered site": (
        "async for t in provider.stream_chat(model, messages, 8192):\n        pass"),
}


@pytest.mark.parametrize("name", sorted(PLANTED_CALLS))
def test_a_chokepoint_call_without_its_context_is_named(name):
    sources = _sources()
    sources[PLANTED_PATH] = (f"async def ask(provider, model, messages):\n"
                             f"    {PLANTED_CALLS[name]}\n")
    calls, problems = context_problems(sources)
    assert any(f == PLANTED_PATH and not ok for f, _ln, _c, ok in calls), (name, calls)
    assert any(PLANTED_PATH in p for p in problems), (name, problems)


# The second verifier's F5: a chokepoint reached without a call this guard
# can read. The first version saw a call's name only, so none of these was
# a site; each is now a reference the guard refuses by name. Then the
# review's R4: the name as a string outside a `getattr` — `attrgetter`,
# `methodcaller`, `__getattribute__`, a `vars(...)` subscript, a name bound
# first — or spelt by a `+` of constants, an f-string, or a dotted path.
PLANTED_REFERENCES = {
    "a bound alias": "f = provider.stream_chat\n    async for t in f(model, messages, num_ctx=8192):\n        pass",
    "a getattr by name": "async for t in getattr(provider, 'stream_chat')(model, messages, num_ctx=8192):\n        pass",
    "a functools.partial": "import functools\n    p = functools.partial(provider.stream_chat, num_ctx=8192)",
    "a chokepoint handed on as an argument": "ask_through(provider.stream_chat, model, messages)",
    "an attrgetter by name": "import operator\n    f = operator.attrgetter('stream_chat')(provider)",
    "a methodcaller by name": "import operator\n    f = operator.methodcaller('stream_chat', model, messages)(provider)",
    "a __getattribute__ by name": "f = provider.__getattribute__('stream_chat')",
    "a vars() subscript by name": "f = vars(type(provider))['stream_chat']",
    "a name bound first and passed to getattr": "n = 'stream_chat'\n    f = getattr(provider, n)",
    "the name spelt by a + of constants": "f = getattr(provider, 'stream_' + 'chat')",
    "the name spelt by an f-string": "f = getattr(provider, f'stream_chat')",
    "the name as the last member of a dotted path": "import operator\n    f = operator.attrgetter('provider.stream_chat')",
}


@pytest.mark.parametrize("name", sorted(PLANTED_REFERENCES))
def test_a_chokepoint_reached_without_a_readable_call_is_named(name):
    sources = _sources()
    sources[PLANTED_PATH] = (f"async def ask(self, provider, model, messages):\n"
                             f"    {PLANTED_REFERENCES[name]}\n")
    _calls, problems = context_problems(sources)
    assert any(PLANTED_PATH in p for p in problems), (name, problems)


def test_an_answered_site_is_checked_not_exempt():
    """The allowances are read, not trusted: ollama.py's positional forwarding
    rewritten as a keyword leaves its entry absent, and named; the same
    site passing a literal instead of the context it received is a call
    that does not pass num_ctx, and named; the script's answered label
    reworded leaves its entry absent, and named, while a second string in
    the same expression that the answer does not cover is refused."""
    path = "backend/providers/ollama.py"
    marker = "self.stream_pieces(model, messages, num_ctx, "
    sources = _sources()
    assert marker in sources[path], "the planting did not take"
    gone = dict(sources, **{path: sources[path].replace(marker, "self.stream_pieces(model, messages, num_ctx=num_ctx, ", 1)})
    _calls, problems = context_problems(gone)
    assert any(p.startswith("answered but absent") and "ollama.py" in p for p in problems), problems
    literal = dict(sources, **{path: sources[path].replace(marker, "self.stream_pieces(model, messages, 4096, ", 1)})
    calls, problems = context_problems(literal)
    assert any(f == path and not ok for f, _ln, _c, ok in calls), calls
    assert any("ollama.py" in p and "without num_ctx" in p for p in problems), problems
    script = "scripts/measure_vocabulary_compliance.py"
    label = '"stream_chat (Ruling 2)"'
    assert label in sources[script], "the planting did not take"
    reworded = dict(sources, **{script: sources[script].replace(label, '"stream_chat"', 1)})
    _calls, problems = context_problems(reworded)
    assert any(p.startswith("answered but absent") and script in p for p in problems), problems
    assert any(p.startswith(f"{script}:") and "stream_chat read by its name as a string" in p for p in problems), problems


def test_the_real_provider_refuses_an_omitted_and_an_oversized_context():
    with pytest.raises(TypeError):
        OllamaProvider().stream_chat("m", [])
    agen = OllamaProvider().stream_chat("m", [], num_ctx=NUM_CTX + 1)
    with pytest.raises(ValueError):
        asyncio.run(agen.__anext__())


# ---------------------------------------------------------------------------
# The two sizes
# ---------------------------------------------------------------------------

def test_extraction_context_is_derived_from_its_cap_and_constant():
    assert extraction_num_ctx(4000) == 4096
    assert extraction_num_ctx() == 4096, "the shipped cap derives 4096"
    assert extraction_num_ctx(16000) == NUM_CTX, "capped at the ceiling"
    assert extraction_num_ctx(500) < 4096, "a smaller cap derives a smaller context"
    # Derived from the CAP, not the text: two very different answers, one size.
    assert extraction_num_ctx(4000) == extraction_num_ctx(4000)


def test_num_ctx_for_is_a_power_of_two_never_above_the_ceiling():
    for chars in (10, 1000, 4674, 40000):
        n = num_ctx_for(chars)
        assert n & (n - 1) == 0 and n <= NUM_CTX
    assert num_ctx_for(4674) == 4096


class _Recorder:
    def __init__(self):
        self.num_ctx = None

    async def stream_chat(self, model, messages, num_ctx):
        self.num_ctx = num_ctx
        yield "[]"

    async def get_embedding(self, t): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return True


def test_extraction_passes_the_derived_size():
    p = _Recorder()
    asyncio.run(extract_triples("TAK-441 is metabolized by CYP3A4.", p))
    assert p.num_ctx == 4096


def test_the_answering_path_states_the_compaction_budget():
    """The answering call names TOKEN_BUDGET, not a literal and not the
    ceiling — pinned by AST on the call — and the budget is at or under
    the ceiling, which is the only relation Law 5 asserts.

    C-38 moved the answering call behind the provider boundary's chokepoint:
    the loop calls `reply_stream(..., num_ctx=TOKEN_BUDGET)` and the
    chokepoint (`backend/providers/base.py`) is the one place that calls
    `stream_chat` for an answer, passing the size it was given through
    unchanged. Both halves are pinned here."""
    src = (ROOT / "backend" / "loop" / "agentic_loop.py").read_text(encoding="utf-8")
    calls = [n for n in ast.walk(ast.parse(src))
             if isinstance(n, ast.Call) and (getattr(n.func, "attr", None) == "stream_chat"
                                             or getattr(n.func, "id", None) == "reply_stream")]
    assert calls
    for c in calls:
        kw = next(k for k in c.keywords if k.arg == "num_ctx")
        assert isinstance(kw.value, ast.Name) and kw.value.id == "TOKEN_BUDGET", ast.unparse(kw)
    base = (ROOT / "backend" / "providers" / "base.py").read_text(encoding="utf-8")
    through = [n for n in ast.walk(ast.parse(base))
               if isinstance(n, ast.Call) and getattr(n.func, "attr", None) == "stream_chat"]
    assert through, "the chokepoint calls stream_chat"
    for c in through:
        kw = next(k for k in c.keywords if k.arg == "num_ctx")
        assert ast.unparse(kw.value) == "self.num_ctx", ast.unparse(kw)
    assert TOKEN_BUDGET <= NUM_CTX


def test_the_ceiling_is_not_the_extraction_size():
    """The literal the packet replaced must not come back under another
    name: extraction's derived size is strictly below the ceiling at the
    shipped cap."""
    assert extraction_num_ctx() < NUM_CTX
    assert extraction.extraction_num_ctx is extraction_num_ctx
