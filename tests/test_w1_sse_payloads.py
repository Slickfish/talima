"""W1 part 2 — docs/CONTRACTS.md's SSE payloads are what the backend emits.

Before W1, 15 of the 21 declarations differed from emission, and nine named a
key no site produces: `token {content}` (the code sends `{t}`, the console
reads `data.t`, INTEGRATION_REFERENCE already said `{t}`); `triples_injected`
and `triples_extracted` declared as objects and emitted as arrays;
`rag_injected.count`, `prediction_generated.nodes`,
`hebbian_update.edges_modified`, `curiosity_surfaced.concept`,
`model_swap_starting.est_ms`, and `kb_pressure_alert.domain` on the
`web_search` producer. tests/test_sse_contract.py reconciles event NAMES and
has never read a payload. H.4's precedent applies: the contract is corrected
to match reality, the emitters are not changed.

P1  THE GUARD. For every declared event, the declaration equals what is
    emitted, derived by AST from every `sse_event(...)` site in the tracked
    backend and, for the bridged events, every `notify_ui({...})` literal.
    The rule: the same shape (object or array); the declared keys are
    exactly the keys every site emits; the keys declared on a `# +` line are
    exactly the rest (a declaration marked `...type-specific fields` may
    leave some of the rest unnamed). A payload this cannot read is a
    failure, unless it is answered for below: four `to_dict()` classes and
    `done`'s builder, each resolved by AST, never by a list of keys.
    FAIL-CLOSED (W1's verifier, F3; the second verifier's lens on part 5a,
    F2 and F3, and its review, R2 and R5): every reference to `sse_event`
    or `notify_ui` is accounted for. A call is read in its positional or
    keyword form; a starred or extra argument, a `notify_ui` payload that
    is not a dict literal, a computed key, a `to_dict` with a return that
    is not a dict literal or on an unanswered receiver, and any other
    reference — an alias, an argument, or the emitter's NAME AS A STRING
    (`getattr(m, "sse_event")`, `globals()["sse_event"]`,
    `getattr(m, "sse_" + "event")`, a patch target
    `"backend.loop.agentic_loop.sse_event"`) — are failures, never
    skipped. WHAT A STRING IS READ AS: a constant, a `+` of constants or
    an f-string's literal parts are folded to one text (the shim guard's
    reader), and the text is a reference when it is an emitter's name
    alone or as the last member of a dotted or colon path — the shapes a
    program resolves an attribute by. Outside the rule by its shape,
    recorded: a text computed at run time (`"SSE_EVENT".lower()`, a `%`
    format, a `join`), and prose that merely contains the name.
    `done`'s payload is a named dict, and every reference to the name
    other than its one assignment, the emit call's argument and the reads
    answered for in NAMED_PAYLOAD_READS (MCP-2's `outcome.update({...,
    "done": _done_payload})`, which hands the frame on) is a reference
    this cannot read: a helper call, an alias, `operator.setitem`, as much
    as a subscript store; the payload's NAME AS A STRING
    (`locals()["_done_payload"]`, `vars()["_done_payload"]`) is one too;
    and the receiver of an answered read (`outcome`) may be referenced
    only in its `is None` / `is not None` test and as the answered call's
    receiver — `outcome["done"]["k"] = v` after the hand-on changes the
    emitted frame unseen, so any other reference to it is refused (R2).
    Each event is declared once (F4): a second line for the same event
    is a failure, not an overwrite. A `# |` line that continues no open
    group (N2) is a grammar failure naming the event and the line, never
    prose. Only top-level keys are reconciled; the nested shapes and
    prose qualifiers in the block's comments are not (docs/CONTRACTS.md says so
    beside the block).
P1c Companions: a declaration naming a key no site emits; an emit site that
    adds a key the declaration lacks; an emit site whose payload cannot be
    read; and, for the fail-closed rules, a keyword-form site adding a key,
    an alias, a starred call, a `notify_ui` payload in a variable, a
    computed key, a `to_dict` with an early return, a receiver nobody
    answered for, the emitter's name as a string (through `getattr`,
    `globals()` and `vars()`, a `+` of constants, an f-string and a dotted
    patch target), eight references to `done`'s payload (a helper call,
    an alias, `operator.setitem`, a read through a call nobody answered
    for, a write through the answered receiver — a subscript store and an
    `.update` on `outcome["done"]` — and the name through `locals()` and
    `vars()`) beside the subscript store, a `# |` line continuing nothing
    (after a closed group, and with no group), and a duplicated stale
    declaration. Each is named.
P2  THE TOKEN, at runtime: every token frame from both emit sites (the
    streamed answer, and C-38's empty-answer line) carries exactly the
    declared key, and the console reads that key.
P3  THE REFERENCE: docs/INTEGRATION_REFERENCE's order table, in both
    copies, agrees cell for cell — event, data AND when (the second
    verifier's F1: the `when` cell was never read, and the live copies
    differed on row 3) — a row with more or fewer cells than the table's
    three in either copy is named before any cell is compared (the
    review's R1: a short row truncated the comparison silently, a long
    one lost its extra cell) — and every payload a row spells out is what
    docs/CONTRACTS.md declares. The row count is pinned, so a row added or removed
    is a deliberate act.
"""
from __future__ import annotations

import ast
import asyncio
import json
import re
from pathlib import Path

import pytest

from tests._tracked import tracked_backend

ROOT = Path(__file__).resolve().parent.parent

# event -> (module, class) whose `to_dict()` IS the payload at its emit sites.
# Answered for by name; the keys are read from the method's returned dict.
TO_DICT_PAYLOADS = {
    "curiosity_surfaced": ("backend/cortex/curiosity.py", "CuriousNode"),
    "compaction_report": ("backend/loop/compaction.py", "CompactionReport"),
    "triples_extracted": ("backend/cortex/extraction.py", "ExtractedTriple"),
    "contradiction_found": ("backend/cortex/contradiction.py", "Contradiction"),
}
# event -> the receivers whose `to_dict()` an emit site may send, as source
# text. The receiver's text is answered for; what it is bound to is not
# traced (a site sending another object's `to_dict()` changes the text).
TO_DICT_RECEIVERS = {
    "curiosity_surfaced": {"cn"},
    "compaction_report": {"cascade.reports[i]"},
    "triples_extracted": {"e"},
    "contradiction_found": {"c"},
}
# event -> the local name its payload is assigned to before the emit.
NAMED_PAYLOADS = {"done": "_done_payload"}
# event -> the calls, by their function's source text, whose one dict-literal
# argument may carry the named payload as a value: a READ that hands the
# frame on. MCP-2's `outcome.update({..., "done": _done_payload})` gives the
# record tool's caller the frame the console was sent, built before the
# emit on the same statement's neighbour. Any other reference to the name is
# one this cannot read (the second verifier's F3: a helper call, an alias
# and `operator.setitem` evaded a rule that read three mutation shapes).
NAMED_PAYLOAD_READS = {"done": {"outcome.update"}}
EMITTERS = ("sse_event", "notify_ui")
# Methods that change a dict in place: a named payload changed after it is
# built is not the payload its assignment shows. Named as such in the
# problem; every other reference is named as unreadable.
MUTATING = {"update", "setdefault", "pop", "popitem", "clear", "__setitem__", "__delitem__"}


def _folded(node) -> str | None:
    """The text a string expression spells with its literal parts: a
    constant, a `+` of constants, or an f-string's literal parts (the shim
    guard's reader, tests/test_w1_shim_retired.py). None for anything
    else, so a text computed at run time is outside the rule by shape."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _folded(node.left), _folded(node.right)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value for v in node.values if isinstance(v, ast.Constant))
    return None


def _name_as_string(node, names) -> str | None:
    """The member of `names` a string expression spells — alone, or as the
    last member of a dotted or colon path (`"m.sse_event"`,
    `"pkg:sse_event"`), the shapes `getattr`, `attrgetter`,
    `pkgutil.resolve_name`, `mock.patch` and a `globals()`/`locals()`
    subscript resolve a name by — or None. The review's R5 and R2: the
    first rule read one `ast.Constant` equal to the name, so a `+` of
    constants reached the emitter unread."""
    text = _folded(node)
    if text is None:
        return None
    m = re.fullmatch(r"(?:[\w.]*[.:])?(\w+)", text)
    return m.group(1) if m and m.group(1) in names else None


def _string_references(tree, names, problem) -> list[str]:
    """Every string expression in `tree` that spells a member of `names`
    (`_name_as_string`), each reported once at its outermost fold — the
    constants inside a reported `+` or f-string are not reported again —
    as `problem(node, name)`."""
    out, skip = [], set()
    for n in ast.walk(tree):
        if id(n) in skip:
            continue
        name = _name_as_string(n, names)
        if name is None:
            continue
        out.append(problem(n, name))
        skip |= {id(d) for d in ast.walk(n) if d is not n}
    return out


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------

def sse_block(md: str | None = None) -> str:
    md = md if md is not None else (ROOT / "docs" / "CONTRACTS.md").read_text(encoding="utf-8")
    return md.split("## SSE Events")[1].split("```")[1]


def _braced(text: str, start: int) -> str:
    """The text inside the bracket at `start`, matching nested brackets."""
    pairs = {"{": "}", "[": "]"}
    stack, i = [], start
    while i < len(text):
        c = text[i]
        if c in pairs:
            stack.append(pairs[c])
        elif stack and c == stack[-1]:
            stack.pop()
            if not stack:
                return text[start + 1:i]
        i += 1
    raise ValueError(f"unclosed bracket at {start}: {text[start:start + 60]!r}")


def declaration_entries(block: str | None = None) -> list[tuple[str, list[str]]]:
    """(event, lines) for each entry in the block, in order: a line that
    starts with a lower-case name opens an entry; the indented lines after
    it belong to it."""
    block = sse_block() if block is None else block
    entries: list[tuple[str, list[str]]] = []
    for line in block.splitlines():
        if re.match(r"^[a-z_]+\s", line):
            entries.append((line.split()[0], [line[len(line.split()[0]):]]))
        elif entries and line.strip():
            entries[-1][1].append(line)
    return entries


def duplicate_declarations(block: str | None = None) -> list[str]:
    """Events that open more than one entry (F4: the parser used to keep the
    last, so a stale line above the corrected one passed)."""
    names = [event for event, _lines in declaration_entries(block)]
    return sorted({n for n in names if names.count(n) > 1})


def _parse_declarations(block: str | None = None) -> tuple[dict[str, dict], list[str]]:
    """(event -> {shape, always, conditional, open}, grammar problems), parsed
    from the block. A grammar problem names the event and quotes the line.
    The one form today: a `# |` line that continues no open group (the
    second verifier's N2 — the first parser dropped it silently, so a key
    declared on it was never reconciled)."""
    out, problems = {}, []
    for event, lines in declaration_entries(block):
        payload, comments = [], []
        for line in lines:
            body, _, comment = line.partition("#")
            payload.append(body)
            if comment.strip():
                comments.append((comment.strip(), line.strip()))
        text = " ".join(payload)
        start = min(i for i in (text.find("{"), text.find("[")) if i >= 0)
        shape = "array" if text[start] == "[" else "object"
        inner = _braced(text, start)
        if shape == "array":
            inner = _braced(inner, inner.index("{"))
        keys = [k.strip() for k in inner.split(",")]
        is_open = any(k.startswith("...") for k in keys)
        always = {k for k in keys if k and not k.startswith("...") and k != "…"}
        conditional = set()
        groups: list[str] = []
        for comment, line in comments:
            # A `+` group lists keys separated by `|` and ends at its first
            # comma. A comment that starts with `|` continues an unfinished
            # group on the next line (N1: a wrapped group was misread). One
            # that continues nothing — no group above it, or a group that
            # has already ended at its comma — is a grammar failure (N2).
            if comment.startswith("+"):
                groups.append(comment[1:])
            elif comment.startswith("|"):
                if groups and "," not in groups[-1]:
                    groups[-1] += " " + comment
                else:
                    problems.append(f"{event}: a `# |` line continues no open group: {line!r}")
        for group in groups:
            conditional |= {k.strip() for k in group.split(",")[0].split("|") if k.strip()}
        out[event] = {"shape": shape, "always": always,
                      "conditional": conditional, "open": is_open}
    return out, problems


def declared_payloads(block: str | None = None) -> dict[str, dict]:
    """event -> {shape, always, conditional, open}, parsed from the block."""
    return _parse_declarations(block)[0]


def declaration_problems(block: str | None = None) -> list[str]:
    """The block's grammar failures, each naming its event and its line."""
    return _parse_declarations(block)[1]


# ---------------------------------------------------------------------------
# The emission
# ---------------------------------------------------------------------------

def _dict_keys(node: ast.Dict, where: str) -> tuple[set, set, list]:
    """(always, conditional, unresolved) keys of a dict literal and its spreads."""
    always, cond, unresolved = set(), set(), []
    for k, v in zip(node.keys, node.values):
        if k is not None:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                always.add(k.value)
            else:
                unresolved.append(f"{where}: a computed key {ast.unparse(k)}")
            continue
        spread = v                                     # `**spread`
        if isinstance(spread, ast.Dict):
            a, c, u = _dict_keys(spread, where)
            always |= a
            cond |= c
            unresolved += u
        elif isinstance(spread, ast.IfExp):
            for branch in (spread.body, spread.orelse):
                if isinstance(branch, ast.Dict):
                    a, c, u = _dict_keys(branch, where)
                    cond |= a | c
                    unresolved += u
                else:
                    unresolved.append(f"{where}: a spread branch {ast.unparse(branch)}")
        elif isinstance(spread, ast.DictComp):
            names = [e.value for cmp in ast.walk(spread) if isinstance(cmp, ast.Compare)
                     for op, rhs in zip(cmp.ops, cmp.comparators)
                     if isinstance(op, ast.In) and isinstance(rhs, (ast.Tuple, ast.List, ast.Set))
                     for e in rhs.elts if isinstance(e, ast.Constant)]
            if names:
                cond |= set(names)
            else:
                unresolved.append(f"{where}: a comprehension spread with no key filter")
        else:
            unresolved.append(f"{where}: a spread of {ast.unparse(spread)}")
    return always, cond, unresolved


def _class_to_dict_keys(module: str, cls: str, sources: dict | None = None) -> tuple[set, set, list]:
    src = (sources or {}).get(module)
    tree = ast.parse(src if src is not None else (ROOT / module).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            for fn in node.body:
                if isinstance(fn, ast.FunctionDef) and fn.name == "to_dict":
                    rets = [r for r in ast.walk(fn) if isinstance(r, ast.Return)]
                    # Every return must be the one dict literal: an early
                    # return of anything else is a payload this cannot read.
                    if len(rets) == 1 and isinstance(rets[0].value, ast.Dict):
                        return _dict_keys(rets[0].value, f"{module}:{cls}.to_dict")
    return set(), set(), [f"{module}:{cls}.to_dict returns no single dict literal"]


def _receiver_problem(event: str, call: ast.Call, where: str) -> list[str]:
    receiver = ast.unparse(call.func.value)
    if receiver not in TO_DICT_RECEIVERS.get(event, set()):
        return [f"{where}: {event} sends the to_dict() of an unanswered receiver {receiver}"]
    return []


def _named_payload_references(fn, name: str, allowed: set, event: str, where: str) -> list[str]:
    """Every reference in `fn` to the dict bound to `name` other than the
    nodes in `allowed` (the assignment's target and the emit call's
    argument) and the reads answered for by name in NAMED_PAYLOAD_READS.
    The three mutation shapes (a subscript store or delete, a mutating
    method, an augmented assignment) are named as a change; everything
    else — a helper call, an alias, `operator.setitem(name, k, v)`, a read
    through a call nobody answered for — is a reference this cannot read.
    Fail-closed (the second verifier's F3). Two more doors, closed by the
    review's R2: the name AS A STRING (`locals()[name]`, `vars()[name]`,
    folded as `_name_as_string` reads), and the RECEIVER of an answered
    read (`outcome`), which holds the same dict after the hand-on — every
    reference to it other than its `is None` / `is not None` test and the
    answered call's own receiver is refused, so `outcome["done"][k] = v`
    is named."""
    parents = {child: parent for parent in ast.walk(fn) for child in ast.iter_child_nodes(parent)}
    allowed = set(allowed)
    receivers: set[str] = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and ast.unparse(n.func) in NAMED_PAYLOAD_READS.get(event, set()) \
                and len(n.args) == 1 and not n.keywords and isinstance(n.args[0], ast.Dict):
            allowed |= {id(v) for v in n.args[0].values if isinstance(v, ast.Name) and v.id == name}
            if isinstance(n.func, ast.Attribute):
                allowed.add(id(n.func.value))
                if isinstance(n.func.value, ast.Name):
                    receivers.add(n.func.value.id)
        if isinstance(n, ast.Compare) and all(isinstance(op, (ast.Is, ast.IsNot)) for op in n.ops) \
                and all(isinstance(c, ast.Constant) and c.value is None for c in n.comparators):
            allowed.add(id(n.left))

    def statement(n):
        stmt = n
        while stmt in parents and not isinstance(stmt, ast.stmt):
            stmt = parents[stmt]
        return ast.unparse(stmt)[:60]

    out = []
    for n in ast.walk(fn):
        if not isinstance(n, ast.Name) or id(n) in allowed:
            continue
        if n.id in receivers:
            out.append(f"{where}: a reference to {n.id}, which carries {name} on, this cannot read: {statement(n)}")
            continue
        if n.id != name:
            continue
        parent = parents.get(n)
        mutation = (
            (isinstance(parent, ast.Subscript) and parent.value is n and isinstance(parent.ctx, (ast.Store, ast.Del)))
            or (isinstance(parent, ast.Attribute) and parent.attr in MUTATING
                and isinstance(parents.get(parent), ast.Call) and parents[parent].func is parent)
            or (isinstance(parent, ast.AugAssign) and parent.target is n))
        if mutation:
            out.append(f"{where}: {name} is changed after it is built: {statement(n)}")
        else:
            out.append(f"{where}: a reference to {name} this cannot read: {statement(n)}")
    out += _string_references(
        fn, {name}, lambda n, nm: f"{where}: {nm} reached by its name as a string: {statement(n)}")
    return out


def _resolve(payload, event: str, fn, where: str, sources: dict | None = None):
    """(shape, always, conditional, unresolved) for one emit site's payload."""
    shape = "object"
    if isinstance(payload, ast.ListComp):
        shape, payload = "array", payload.elt
    if isinstance(payload, ast.Dict):
        return (shape, *_dict_keys(payload, where))
    if (isinstance(payload, ast.Call) and isinstance(payload.func, ast.Attribute)
            and payload.func.attr == "to_dict" and event in TO_DICT_PAYLOADS):
        a, c, u = _class_to_dict_keys(*TO_DICT_PAYLOADS[event], sources)
        return shape, a, c, u + _receiver_problem(event, payload, where)
    if isinstance(payload, ast.Name) and NAMED_PAYLOADS.get(event) == payload.id and fn is not None:
        assigns = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
                   and any(getattr(t, "id", None) == payload.id for t in n.targets)]
        if len(assigns) == 1 and isinstance(assigns[0].value, ast.Dict):
            a, c, u = _dict_keys(assigns[0].value, where)
            allowed = {id(t) for t in assigns[0].targets} | {id(payload)}
            return shape, a, c, u + _named_payload_references(fn, payload.id, allowed, event, where)
    return shape, set(), set(), [f"{where}: an unreadable payload {ast.unparse(payload)[:60]}"]


def _call_args(call: ast.Call, names: tuple) -> tuple | None:
    """The call's arguments bound to `names` (positional or keyword), or
    None if any argument is starred, extra or missing."""
    if any(isinstance(a, ast.Starred) for a in call.args) or len(call.args) > len(names):
        return None
    bound = dict(zip(names, call.args))
    for kw in call.keywords:
        if kw.arg is None or kw.arg not in names or kw.arg in bound:
            return None
        bound[kw.arg] = kw.value
    return tuple(bound[n] for n in names) if len(bound) == len(names) else None


def _other_references(tree, calls_func: set, where_of) -> list[str]:
    """References to an emitter that are not the function of a call: allowed
    only as an assignment target (the bridge's wiring) or in an `is None` /
    `is not None` test. Anything else (an alias, an argument, a renamed
    import, or the emitter's name as a string — `getattr(m, "sse_event")`,
    `globals()["sse_event"]`, `getattr(m, "sse_" + "event")`, a patch
    target `"backend.loop.agentic_loop.sse_event"`, a dict of handlers
    keyed by name; the second verifier's F2 and the review's R5) would
    emit without this guard seeing it. A string is read folded and as a
    name or a path ending in one (`_name_as_string`); the tracked backend
    holds no such string today, by AST."""
    allowed = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Compare) and all(isinstance(op, (ast.Is, ast.IsNot)) for op in n.ops) \
                and all(isinstance(c, ast.Constant) and c.value is None for c in n.comparators):
            allowed.add(id(n.left))
    out = []
    for n in ast.walk(tree):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else None
        if name in EMITTERS and id(n) not in calls_func and id(n) not in allowed \
                and not isinstance(n.ctx, ast.Store):
            out.append(f"{where_of(n)}: a reference to {name} that is not a call: {ast.unparse(n)}")
        if isinstance(n, (ast.ImportFrom, ast.Import)):
            for alias in n.names:
                if alias.name.split(".")[-1] in EMITTERS and alias.asname is not None:
                    out.append(f"{where_of(n)}: {alias.name} imported as {alias.asname}")
    out += _string_references(
        tree, set(EMITTERS),
        lambda n, nm: f"{where_of(n)}: a reference to {nm} that is not a call: the string {ast.unparse(n)}")
    return out


def _functions(tree):
    out = []

    class V(ast.NodeVisitor):
        def __init__(self):
            self.stack = [None]

        def _fn(self, node):
            self.stack.append(node)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = _fn

        def visit_Call(self, node):
            out.append((node, self.stack[-1]))
            self.generic_visit(node)

    V().visit(tree)
    return out


def emitted_payloads(sources: dict[str, str] | None = None) -> tuple[dict, list]:
    """event -> {shape, sites: [(always, conditional)]}, and unresolved sites."""
    if sources is None:
        sources = {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8")
                   for p in tracked_backend()}
    sites: dict[str, list] = {}
    unresolved: list[str] = []
    bridge_sites = 0
    for path, src in sorted(sources.items()):
        tree = ast.parse(src)
        calls = _functions(tree)
        calls_func = {id(call.func) for call, _fn in calls}
        unresolved += _other_references(tree, calls_func, lambda n, p=path: f"{p}:{n.lineno}")
        for call, fn in calls:
            f = call.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
            where = f"{path}:{call.lineno}"
            if name == "sse_event":
                bound = _call_args(call, ("event", "data"))
                if bound is None:
                    unresolved.append(f"{where}: an sse_event call this cannot bind: {ast.unparse(call)[:60]}")
                    continue
                head, payload = bound
                if not (isinstance(head, ast.Constant) and isinstance(head.value, str)):
                    bridge_sites += 1           # the KAIROS bridge: resolved below
                    continue
                shape, a, c, u = _resolve(payload, head.value, fn, where, sources)
                sites.setdefault(head.value, []).append((shape, a, c))
                unresolved += u
            elif name == "notify_ui":
                if call.keywords or len(call.args) != 1 or not isinstance(call.args[0], ast.Dict):
                    unresolved.append(f"{where}: a notify_ui payload that is not one dict literal: "
                                      f"{ast.unparse(call)[:60]}")
                    continue
                d = call.args[0]
                ev = next((v.value for k, v in zip(d.keys, d.values)
                           if isinstance(k, ast.Constant) and k.value == "event"
                           and isinstance(v, ast.Constant)), None)
                if ev is None:
                    unresolved.append(f"{where}: a notify_ui payload with no event name")
                    continue
                a, c, u = set(), set(), []
                for k, v in zip(d.keys, d.values):
                    if k is None and isinstance(v, ast.Call) and getattr(v.func, "attr", "") == "to_dict" \
                            and ev in TO_DICT_PAYLOADS:
                        a2, c2, u2 = _class_to_dict_keys(*TO_DICT_PAYLOADS[ev], sources)
                        a |= a2
                        c |= c2
                        u += u2 + _receiver_problem(ev, v, where)
                    elif k is None:
                        u.append(f"{where}: a spread of {ast.unparse(v)}")
                    elif not (isinstance(k, ast.Constant) and isinstance(k.value, str)):
                        u.append(f"{where}: a computed key {ast.unparse(k)} in a notify_ui payload")
                    elif k.value != "event":
                        a.add(k.value)
                sites.setdefault(ev, []).append(("object", a, c))
                unresolved += u
    if bridge_sites != 1:
        unresolved.append(f"{bridge_sites} sse_event sites with a computed name (the bridge is one)")
    return sites, unresolved


def reconcile(declared: dict, sites: dict) -> list[str]:
    problems = []
    for event, d in sorted(declared.items()):
        emitted = sites.get(event)
        if not emitted:
            problems.append(f"{event}: declared, but no emit site was found")
            continue
        shapes = {s for s, _a, _c in emitted}
        every = set.intersection(*[a for _s, a, _c in emitted])
        some = set().union(*[a | c for _s, a, c in emitted]) - every
        if shapes != {d["shape"]}:
            problems.append(f"{event}: declared {d['shape']}, emitted {sorted(shapes)}")
        if d["always"] != every:
            problems.append(f"{event}: declared {sorted(d['always'])}, every site emits "
                            f"{sorted(every)} (never emitted: {sorted(d['always'] - every)}; "
                            f"undeclared: {sorted(every - d['always'])})")
        extra = d["conditional"] - some
        missing = some - d["conditional"]
        if extra:
            problems.append(f"{event}: declared on a `+` line but never emitted: {sorted(extra)}")
        if missing and not d["open"]:
            problems.append(f"{event}: emitted by some sites but not declared: {sorted(missing)}")
    for event in sorted(set(sites) - set(declared)):
        problems.append(f"{event}: emitted, but not declared")
    return problems


def test_p1_every_declared_payload_is_what_the_backend_emits():
    declared = declared_payloads()
    sites, unresolved = emitted_payloads()
    print(f"P1 declared {len(declared)} events; emit sites by event: "
          f"{ {e: len(s) for e, s in sorted(sites.items())} }")
    assert not duplicate_declarations(), f"declared more than once: {duplicate_declarations()}"
    assert not declaration_problems(), f"the block's grammar: {declaration_problems()}"
    assert len(declared) == 22, sorted(declared)      # W6: record_failed
    assert not unresolved, f"payloads the guard cannot read: {unresolved}"
    problems = reconcile(declared, sites)
    assert not problems, "docs/CONTRACTS.md's SSE payloads disagree with the code:\n  " + "\n  ".join(problems)


def _agentic_source() -> dict[str, str]:
    return {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in tracked_backend()}


def test_p1c_a_declared_key_no_site_emits_is_named():
    block = sse_block().replace("token                 {t}", "token                 {t, text}")
    assert "{t, text}" in block, "the planting did not take"
    problems = reconcile(declared_payloads(block), emitted_payloads()[0])
    assert any(p.startswith("token:") and "text" in p for p in problems), problems


def test_p1c_an_emit_site_adding_an_undeclared_key_is_named():
    sources = _agentic_source()
    path = "backend/loop/agentic_loop.py"
    sources[path] += ('\n\nasync def _planted(state):\n'
                      '    yield sse_event("token", {"t": "x", "chars": 1}), state\n')
    problems = reconcile(declared_payloads(), emitted_payloads(sources)[0])
    assert any(p.startswith("token:") and "chars" in p for p in problems), problems


def test_p1c_an_unreadable_payload_is_named():
    sources = _agentic_source()
    path = "backend/loop/agentic_loop.py"
    sources[path] += ('\n\nasync def _planted(state, built):\n'
                      '    yield sse_event("token", built), state\n')
    unresolved = emitted_payloads(sources)[1]
    assert any("unreadable payload built" in u for u in unresolved), unresolved


# FAIL-CLOSED companions (W1's verifier, F3 and F4). Each plants a form the
# first version of P1 skipped with the whole suite green, and asserts the
# consequence: P1's own assertions fail, naming it.

def _p1_problems(sources: dict | None = None, block: str | None = None) -> list[str]:
    """Everything P1 asserts on, as one list: duplicates, unreadable sites and
    disagreements. Empty means P1 passes."""
    sites, unresolved = emitted_payloads(sources)
    dups = [f"declared more than once: {d}" for d in duplicate_declarations(block)]
    return dups + declaration_problems(block) + unresolved + reconcile(declared_payloads(block), sites)


def _planted(code: str, path: str = "backend/loop/agentic_loop.py") -> dict:
    sources = _agentic_source()
    sources[path] += "\n\n" + code + "\n"
    return sources


FAIL_CLOSED = {
    "a keyword-form site adding a key": (
        'async def _planted(state):\n    yield sse_event("token", data={"t": "x", "extra": 1}), state',
        "extra"),
    "an alias of the emitter": (
        '_emit = sse_event\nasync def _planted(state):\n    yield _emit("token", {"t": "x", "extra": 1}), state',
        "a reference to sse_event that is not a call"),
    "a starred call": (
        'async def _planted(state):\n    yield sse_event(*("token", {"t": "x"})), state',
        "an sse_event call this cannot bind"),
    "a notify_ui payload in a variable": (
        'async def _planted(self, cn):\n    payload = {"event": "curiosity_surfaced", "extra": 1}\n'
        '    await self.notify_ui(payload)',
        "not one dict literal"),
    "a computed key in a notify_ui literal": (
        'async def _planted(self, k):\n    await self.notify_ui({"event": "chain_appended", k: 1})',
        "a computed key"),
    "a to_dict on an unanswered receiver": (
        'async def _planted(state, other):\n    yield sse_event("contradiction_found", other.to_dict()), state',
        "unanswered receiver other"),
    # The second verifier's F2: the emitter reached by its name as a string.
    # The class is the FORM (a string-keyed read), of which the guard's
    # source names none — it refuses any string spelling an emitter's
    # name — so three forms over both names are planted; then the review's
    # R5: the name spelt by a `+` of constants, by an f-string, and as the
    # last member of a dotted patch target, each of which one literal
    # compared whole did not read.
    "the emitter's name as a string, through getattr": (
        'import backend.loop.agentic_loop as _m\nasync def _planted(state):\n'
        '    yield getattr(_m, "sse_event")("token", {"t": "x", "extra": 1}), state',
        "a reference to sse_event that is not a call"),
    "the emitter's name as a string, through globals()": (
        'async def _planted(state):\n    yield globals()["sse_event"]("token", {"t": "x", "extra": 1}), state',
        "a reference to sse_event that is not a call"),
    "notify_ui's name as a string, through vars()": (
        'async def _planted(self):\n    await vars(self)["notify_ui"]({"event": "chain_appended", "extra": 1})',
        "a reference to notify_ui that is not a call"),
    "the emitter's name spelt by a + of constants": (
        'import backend.loop.agentic_loop as _m\nasync def _planted(state):\n'
        '    yield getattr(_m, "sse_" + "event")("token", {"t": "x", "extra": 1}), state',
        "a reference to sse_event that is not a call"),
    "the emitter's name spelt by an f-string": (
        'import backend.loop.agentic_loop as _m\nasync def _planted(state):\n'
        '    yield getattr(_m, f"sse_event")("token", {"t": "x", "extra": 1}), state',
        "a reference to sse_event that is not a call"),
    "the emitter's name as the last member of a dotted path": (
        'import pkgutil\n_emit = pkgutil.resolve_name("backend.loop.agentic_loop.sse_event")\n'
        'async def _planted(state):\n    yield _emit("token", {"t": "x", "extra": 1}), state',
        "a reference to sse_event that is not a call"),
}


@pytest.mark.parametrize("name", sorted(FAIL_CLOSED))
def test_p1c_a_form_the_guard_used_to_skip_is_named(name):
    code, expected = FAIL_CLOSED[name]
    problems = _p1_problems(_planted(code))
    assert any(expected in p for p in problems), (name, problems)


def test_p1c_a_to_dict_with_an_early_return_is_named():
    sources = _agentic_source()
    path = "backend/cortex/contradiction.py"
    marker = "    def to_dict(self) -> dict:\n"
    assert marker in sources[path], "the planting did not take"
    sources[path] = sources[path].replace(
        marker, marker + "        if self.subject is None:\n            return dict(extra=1)\n", 1)
    problems = _p1_problems(sources)
    assert any("Contradiction.to_dict returns no single dict literal" in p for p in problems), problems


def test_p1c_done_changed_after_it_is_built_is_named():
    sources = _agentic_source()
    path = "backend/loop/agentic_loop.py"
    marker = '    yield sse_event("done", _done_payload), new_state\n'
    assert marker in sources[path], "the planting did not take"
    sources[path] = sources[path].replace(marker, '    _done_payload["sneaky"] = 1\n' + marker, 1)
    problems = _p1_problems(sources)
    assert any("_done_payload is changed after it is built" in p for p in problems), problems


# The second verifier's F3: four references to `done`'s payload that the
# first rule (a subscript store, seven mutating methods, an augmented
# assignment) did not read, each planted before the emit. The fourth is a
# read through a call the allowance does not name, so the allowance is shown
# to be keyed on `outcome.update` and not on `.update({...})`. Then the
# review's R2: four doors through the allowance itself — a write through
# the answered receiver (`outcome["done"]`, a subscript store and an
# `.update`), which is the same dict after the hand-on, and the payload's
# name as a string through `locals()` and `vars()` (CPython returns the
# same dict object, so the emitted frame gains the key). Each value is
# (the planted lines, the problem the check must name).
DONE_REFERENCES = {
    "a helper call on the payload": (
        "    _fill(_done_payload)\n", "a reference to _done_payload this cannot read"),
    "an alias of the payload": (
        "    _d = _done_payload\n    _d['sneaky'] = 1\n", "a reference to _done_payload this cannot read"),
    "operator.setitem on the payload": (
        "    import operator\n    operator.setitem(_done_payload, 'sneaky', 1)\n",
        "a reference to _done_payload this cannot read"),
    "a read through a call nobody answered for": (
        "    other.update({'done': _done_payload})\n", "a reference to _done_payload this cannot read"),
    "a subscript store through the answered receiver": (
        "    outcome['done']['sneaky'] = 1\n", "a reference to outcome, which carries _done_payload on"),
    "an update through the answered receiver": (
        "    outcome['done'].update({'sneaky': 1})\n", "a reference to outcome, which carries _done_payload on"),
    "the payload's name through locals()": (
        "    locals()['_done_payload']['sneaky'] = 1\n", "_done_payload reached by its name as a string"),
    "the payload's name through vars()": (
        "    vars()['_done_payload']['sneaky'] = 1\n", "_done_payload reached by its name as a string"),
}


@pytest.mark.parametrize("name", sorted(DONE_REFERENCES))
def test_p1c_a_reference_to_done_the_guard_cannot_read_is_named(name):
    sources = _agentic_source()
    path = "backend/loop/agentic_loop.py"
    marker = '    yield sse_event("done", _done_payload), new_state\n'
    assert marker in sources[path], "the planting did not take"
    planted, expected = DONE_REFERENCES[name]
    sources[path] = sources[path].replace(marker, planted + marker, 1)
    problems = _p1_problems(sources)
    assert any(expected in p for p in problems), (name, problems)


def test_p1c_a_continuation_that_continues_nothing_is_named():
    """N2 (the second verifier): a `# |` line after a group that has already
    ended at its comma, and one with no group above it, each planted into
    the block. The first parser dropped both silently, so a key declared
    there was never reconciled; the grammar paragraph beside the block
    says so now."""
    block = sse_block()
    closed = next(l for l in block.splitlines() if "# + citation_forecast" in l)
    planted = block.replace(closed, closed + "\n                       # | sneaky_key", 1)
    assert planted != block, "the planting did not take"
    problems = _p1_problems(block=planted)
    assert any(p.startswith("done: a `# |` line continues no open group") and "sneaky_key" in p
               for p in problems), problems
    head = "turn_start            {turn}"
    planted = block.replace(head, head + "\n                       # | sneaky_key", 1)
    assert planted != block, "the planting did not take"
    problems = _p1_problems(block=planted)
    assert any(p.startswith("turn_start: a `# |` line continues no open group") for p in problems), problems


def test_p1c_a_duplicated_stale_declaration_is_named():
    """F4: the exact pre-W1 lines, left above the corrected ones."""
    block = sse_block().replace("token                 {t}",
                                "token                 {content}\ntoken                 {t}", 1)
    assert "{content}" in block, "the planting did not take"
    problems = _p1_problems(block=block)
    assert any("declared more than once: token" in p for p in problems), problems


def test_a_wrapped_plus_group_reads_as_one_group():
    """N1: the `+` grammar, declared beside the block in docs/CONTRACTS.md. A group
    wrapped onto a comment line that starts with `|` is the same group."""
    block = sse_block()
    line = next(l for l in block.splitlines() if "# + unit_already_closed" in l)
    head, tail = line.split(" | boundary_not_applied", 1)
    wrapped = block.replace(line, head + "\n                       # | boundary_not_applied" + tail, 1)
    assert wrapped != block, "the planting did not take"
    assert declared_payloads(wrapped)["done"] == declared_payloads(block)["done"]


# ---------------------------------------------------------------------------
# P2 — the token, at runtime, from both emit sites
# ---------------------------------------------------------------------------

def test_p2_every_token_frame_carries_the_declared_key_and_the_console_reads_it(tmp_path, monkeypatch):
    from tests.test_c38_reply_separation import _Chat, _plant, _turn, ANSWER
    from backend.loop.agentic_loop import ANSWER_EMPTY_CONSOLE_MESSAGE
    from backend.memory.lineage import LineageStore
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    monkeypatch.delenv("NEXUS_REPLY_SEPARATION", raising=False)
    declared = declared_payloads()["token"]["always"]
    frames_seen = []
    import backend.loop.agentic_loop as loop
    real = loop.sse_event

    def recording(event, data):
        if event == "token":
            frames_seen.append(data)
        return real(event, data)
    monkeypatch.setattr(loop, "sse_event", recording)

    store, lineage = _plant(tmp_path), LineageStore(tmp_path / "kb.db")
    _state, _done, streamed = _turn(store, lineage, _Chat([ANSWER[:20], ANSWER[20:]]))
    _state2, done2, empty = _turn(store, lineage, _Chat(["<think>", "still thinking"]))
    store.close(); lineage.close()
    print(f"P2 token frames: {frames_seen!r}")
    assert streamed and empty == [ANSWER_EMPTY_CONSOLE_MESSAGE] and done2["answer_empty"] is True
    # N6: the empty-answer line is itself one of the frames recorded here,
    # so a move of that emit site out of this module would show.
    assert {"t": ANSWER_EMPTY_CONSOLE_MESSAGE} in frames_seen, frames_seen
    assert frames_seen and all(set(f) == declared for f in frames_seen), (
        f"a token frame's keys differ from the declared {sorted(declared)}: {frames_seen}")
    ui = (ROOT / "frontend" / "nexus_ui.html").read_text(encoding="utf-8")
    case = ui[ui.index("case 'token':"):][:200]
    read = set(re.findall(r"\bdata\.(\w+)", case.split("break")[0]))
    assert read == declared, f"the console reads {sorted(read)} from a token frame"


# ---------------------------------------------------------------------------
# P3 — INTEGRATION_REFERENCE's order table, in both files (W1's verifier, F6)
# ---------------------------------------------------------------------------

import html as _html  # noqa: E402

REFERENCES = (ROOT / "docs" / "INTEGRATION_REFERENCE.md", ROOT / "docs" / "INTEGRATION_REFERENCE.html")


CELLS = ("event", "data", "when")


def _norm(cell: str) -> str:
    """A cell as plain text: the markdown's emphasis and code marks removed
    (`*`, so `**bold**` and `*em*` alike; backticks) — the HTML copy's tags
    are stripped and its entities unescaped before this — and whitespace
    collapsed."""
    return re.sub(r"\s+", " ", cell.replace("*", "").replace("`", "")).strip()


def reference_rows(md: str | None = None, html: str | None = None) -> tuple[dict, dict]:
    """row number -> the row's content cells (event, data, when — every
    cell after the number, however many the row has) of the SSE order
    table, read from the markdown and from the HTML, each normalised to
    plain text. All three content cells (the second verifier's F1: the
    first version kept the event and data cells and dropped `when`, so the
    copies could differ there unseen — and did, on row 3); nothing is
    sliced off, so a row with a cell too few or too many is seen as such
    (the review's R1). The markdown's escaped pipe `\\|` reads as `|`
    (row 20 carries two)."""
    md = REFERENCES[0].read_text(encoding="utf-8") if md is None else md
    html = REFERENCES[1].read_text(encoding="utf-8") if html is None else html
    md_rows = {}
    for line in md[md.index("| # | event | `data` | when |"):].splitlines()[2:]:
        if not line.startswith("|"):
            break
        cells = [c.strip().replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line)[1:-1]]
        md_rows[cells[0]] = tuple(_norm(c) for c in cells[1:])
    start = html.index("<th>event</th>")
    html_rows = {}
    for tr in re.findall(r"<tr>(.*?)</tr>", html[start:html.index("</table>", start)], re.S):
        cells = [_norm(_html.unescape(re.sub(r"<[^>]+>", "", c))) for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if cells:
            html_rows[cells[0]] = tuple(cells[1:])
    return md_rows, html_rows


def _top_keys(inner: str) -> tuple[set, bool]:
    parts, depth, cur = [], 0, ""
    for ch in inner:
        depth += ch in "{[" and 1 or 0
        depth -= ch in "}]" and 1 or 0
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    parts.append(cur)
    keys, is_open = set(), False
    for part in (p.strip() for p in parts):
        if part.startswith(("…", "...")):
            is_open = True
        elif part:
            keys.add(part.split(":")[0].replace("[]", "").strip())
    return keys, is_open


def reference_problems(md: str | None = None, html: str | None = None,
                       declared: dict | None = None) -> list[str]:
    """The table's two copies must agree cell for cell — event, data and
    when, each named when it differs — and every payload a row spells out
    must be what docs/CONTRACTS.md declares: its shape, no key the declaration
    lacks, and, unless the row is open, exactly the keys every site sends.
    `kb_pressure_alert`'s rows list one producer's type-specific keys,
    which docs/CONTRACTS.md declares open; only `type` is checked there."""
    md_rows, html_rows = reference_rows(md, html)
    declared = declared_payloads() if declared is None else declared
    problems = []
    for n in sorted(set(md_rows) | set(html_rows), key=int):
        a, b = md_rows.get(n), html_rows.get(n)
        if a is None or b is None:
            problems.append(f"row {n}: in the {'markdown' if a else 'HTML'} only")
            continue
        # Arity before content (the review's R1): a `zip` over two rows of
        # different length stopped at the shorter one, so a `when` cell
        # dropped from one copy was never compared, and a row with a cell
        # too many lost it silently.
        if len(a) != len(CELLS) or len(b) != len(CELLS):
            problems.append(f"row {n}: {len(a)} cells after the number in the markdown, {len(b)} in the HTML; "
                            f"the table has {len(CELLS)}")
            continue
        for cell, x, y in zip(CELLS, a, b):
            if x != y:
                problems.append(f"row {n}: the {cell} cell differs: the markdown says {x!r}, the HTML {y!r}")
    for n, cells in sorted(md_rows.items(), key=lambda kv: int(kv[0])):
        if len(cells) != len(CELLS):
            continue                            # named above; nothing to read
        event_cell, data_cell = cells[0], cells[1]
        found = [(m.group(1), "object", *_top_keys(_braced(event_cell, m.end() - 1)))
                 for m in re.finditer(r"([a-z_]+) \{", event_cell)]
        if data_cell.startswith("array of"):
            found.append((event_cell.split()[0], "array", *_top_keys(_braced(data_cell, data_cell.index("{")))))
        for event, shape, keys, is_open in found:
            d = declared.get(event)
            if d is None:
                problems.append(f"row {n}: {event} is not declared")
                continue
            if event == "kb_pressure_alert":
                if "type" not in keys:
                    problems.append(f"row {n}: a kb_pressure_alert row without its type")
                continue
            if shape != d["shape"]:
                problems.append(f"row {n}: {event} is a {shape} here and declared an {d['shape']}")
            if keys - (d["always"] | d["conditional"]):
                problems.append(f"row {n}: {event} names undeclared {sorted(keys - (d['always'] | d['conditional']))}")
            if not is_open and keys != d["always"]:
                problems.append(f"row {n}: {event} is closed here but lists {sorted(keys)}, "
                                f"not the declared {sorted(d['always'])}")
    return problems


def test_p3_the_integration_reference_agrees_with_itself_and_the_declarations():
    """The two copies agree cell for cell — event, data and when — and every
    payload a row spells out is what docs/CONTRACTS.md declares. The row count is
    pinned at 21 on purpose (20 until W6 added `record_failed`): a row added to or removed from the table is a
    deliberate act, made here beside the documents and never inferred from
    them (the second verifier's tested-not-declared note)."""
    md_rows, html_rows = reference_rows()
    print(f"P3 rows: markdown {len(md_rows)}, HTML {len(html_rows)}")
    assert len(md_rows) == len(html_rows) == 21, (sorted(md_rows), sorted(html_rows))
    assert reference_problems() == []


def test_p3c_the_old_token_row_is_named():
    """Companion: the defect W1 began with, `token {content}`, planted into the
    markdown alone. The copies disagree, and the key is not declared."""
    md = REFERENCES[0].read_text(encoding="utf-8")
    planted = md.replace("| 13 | `token` `{t}` |", "| 13 | `token` `{content}` |", 1)
    assert planted != md, "the planting did not take"
    problems = reference_problems(md=planted)
    assert any(p.startswith("row 13: the event cell differs") for p in problems), problems
    assert any("token names undeclared ['content']" in p for p in problems), problems


# The second verifier's F1: a `when`-only divergence, planted in one copy at
# a time. Row 6's wording changed in the markdown, the same in the HTML, and
# row 10's empty cell filled in the markdown (the lens's two surviving
# mutants); none of these rows or words is named by the check.
WHEN_DIVERGENCES = {
    "row 6 reworded in the markdown alone": (
        "md", "| object | every turn — `locked`", "| object | most turns — `locked`", "6"),
    "row 6 reworded in the HTML alone": (
        "html", "<td>every turn — <code>locked</code>", "<td>most turns — <code>locked</code>", "6"),
    "row 10's empty cell filled in the markdown alone": (
        "md", "| 10 | `turn_start` `{turn}` | object | |", "| 10 | `turn_start` `{turn}` | object | the turn begins |", "10"),
}


@pytest.mark.parametrize("name", sorted(WHEN_DIVERGENCES))
def test_p3c_a_when_cell_changed_in_one_copy_is_named(name):
    which, old, new, row = WHEN_DIVERGENCES[name]
    md, html = (p.read_text(encoding="utf-8") for p in REFERENCES)
    text = md if which == "md" else html
    planted = text.replace(old, new, 1)
    assert planted != text, "the planting did not take"
    problems = reference_problems(md=planted) if which == "md" else reference_problems(html=planted)
    assert any(p.startswith(f"row {row}: the when cell differs") for p in problems), (name, problems)
    assert not any(p.startswith(f"row {row}: the event cell") or p.startswith(f"row {row}: the data cell")
                   for p in problems), (name, problems)


# The review's R1: a row with a cell too few or too many in one copy. Row
# 10's `when` cell dropped from the HTML (the lens's evasion: `zip` stopped
# at the shorter row and named nothing), the same row written with three
# cells in the markdown (the first version crashed on the unpack instead of
# naming the row), and a fifth cell added to it in the markdown (sliced off
# unseen). None of these rows or counts is named by the check.
ARITY_DIVERGENCES = {
    "row 10's when cell dropped from the HTML alone": (
        "html", "<td><code>turn_start</code> <code>{turn}</code></td><td>object</td><td></td></tr>",
        "<td><code>turn_start</code> <code>{turn}</code></td><td>object</td></tr>",
        "row 10: 3 cells after the number in the markdown, 2 in the HTML; the table has 3"),
    "row 10 written with three cells in the markdown alone": (
        "md", "| 10 | `turn_start` `{turn}` | object | |", "| 10 | `turn_start` `{turn}` | object |",
        "row 10: 2 cells after the number in the markdown, 3 in the HTML; the table has 3"),
    "row 10 given a fifth cell in the markdown alone": (
        "md", "| 10 | `turn_start` `{turn}` | object | |", "| 10 | `turn_start` `{turn}` | object | | extra |",
        "row 10: 4 cells after the number in the markdown, 3 in the HTML; the table has 3"),
}


@pytest.mark.parametrize("name", sorted(ARITY_DIVERGENCES))
def test_p3c_a_row_with_the_wrong_number_of_cells_in_one_copy_is_named(name):
    which, old, new, expected = ARITY_DIVERGENCES[name]
    md, html = (p.read_text(encoding="utf-8") for p in REFERENCES)
    text = md if which == "md" else html
    planted = text.replace(old, new, 1)
    assert planted != text, "the planting did not take"
    problems = reference_problems(md=planted) if which == "md" else reference_problems(html=planted)
    assert expected in problems, (name, problems)
    assert not any(p.startswith("row 10: the ") for p in problems), (name, problems)


def test_p3c_a_closed_row_missing_a_declared_key_is_named():
    """Companion: a closed row that drops a key every site sends, planted
    into both copies, so the copies still agree with each other."""
    md, html = (p.read_text(encoding="utf-8") for p in REFERENCES)
    md2 = md.replace("`{turn}`", "`{}`", 1)
    html2 = html.replace("<code>{turn}</code>", "<code>{}</code>", 1)
    assert md2 != md and html2 != html, "the planting did not take"
    problems = reference_problems(md=md2, html=html2)
    assert any("turn_start is closed here but lists []" in p for p in problems), problems
