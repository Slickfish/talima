"""An exception after the token loop truncates the stream silently.

THE DEFECT, REPRODUCED BIT-FOR-BIT
    The operator's probe recorded this frame set and nothing after it:

        kairos_consolidation, prediction_generated, model_pull_proposal,
        model_routed, turn_start, triples_injected, graph_activating,
        token×241                      ... and then the stream ends.

        done payload : NO done FRAME
        prov-toggle  : NEVER ADDED OR REMOVED

    Every frontend mechanism checked out because the frontend is not
    involved. The `/chat` route's stream generator (`gen()` in
    `backend/server.py`) is `try:` / `finally:` with **no `except`**, so
    an exception raised anywhere in `run_turn` after the token loop propagates
    out of `gen()`. The response has already been committed — HTTP 200, headers
    sent, tokens delivered — so no error response can be substituted. Starlette
    logs it and closes the connection mid-chunk. The client sees a truncated
    body and, in a browser, `fetch`'s reader simply reports done.

    The lineage record survives because `complete_turn` writes it
    (`lineage.record_turn`) inside its own `try`/`except`, BEFORE the stretch
    where the operator's turn died. That is why the operator's counts kept
    rising while the frame never arrived: the turn's record exists and the
    turn's report does not.

WHY IT WAS INVISIBLE FOR FOUR ROUNDS
    Uvicorn prints the whole traceback, naming the exact `agentic_loop.py`
    line. It was in the server console the entire time — underneath 5,235
    consolidation refusal lines per cycle, every five minutes. "A log nobody
    can read is a log that hides the next finding" was written about this
    stream the day before it hid this.

THE WINDOW (named by construct, never by line: W1 found every line number
this docstring once carried stale. `test_the_window_is_where_this_says`
below checks, by AST, every claim this section makes about a construct;
`window_claims` lists them, and each has a planted companion)
    MCP-2 split the turn: `run_turn` reads the model's stream and hands the
    reply to `complete_turn`, which writes the record and emits everything
    after it, ending with `done`. Since W6 `run_turn` holds the answer's
    `token` frames under the default release and `complete_turn` releases
    them right after the record step (under `NEXUS_ANSWER_RELEASE=live`
    `run_turn` yields them as they arrive, as it did when this was written),
    so a raise before the record step now ends the stream with no answer
    sent, and a raise after it ends the stream as the operator's probe saw.
    `run_turn` has no `try` at all.
    `complete_turn`'s handlers each guard one write: the extraction's `add()`
    calls before the record, the record itself, and after it only the
    boundary act, the unit admission and the forecast score. Nothing in
    either function catches
    what escapes those. Between the last `token` and `done`, the one call in
    `complete_turn` that reaches SQLite unguarded is `retriever.store.fire`,
    an UPDATE plus commit (a write the freeze skips, FZ-2). `hebbian.update`
    also reads SQLite, but contains the failure. Other raises are not SQLite:
    for instance the history carrier's `CarrierMismatch`, on a carrier and a
    history that disagree. What a callee does inside itself is checked only
    for the callees named here; that `fire` is the only site at run time was
    measured by a SQL trace in W1's establish (1 November), and is not
    re-measured by this file. The check reads calls by LINE ORDER — a call
    is after the record when its line is below the record's — so a helper
    defined inside `complete_turn` above the record and called below it
    would not be read; the check therefore refuses any function or lambda
    defined inside `complete_turn` above the record (none today; the one
    nested helper, `_r`, sits below it). A `with` is a handler the
    `try`-shaped rules cannot see (`contextlib.suppress` catches without a
    `try`), so every `with` inside `gen()`, `run_turn`, `complete_turn` and
    `align_carrier` — the functions whose claim is that nothing catches, or
    that a raise reaches its caller — is refused unless its context
    expression is answered for by name: `gen()`'s `services.graph_lock`, a
    lock whose exit suppresses nothing, is the one. A `try`/`except*`
    (Python 3.11's `ast.TryStar`) catches like `except` and is read as a
    handled try wherever a `try` is. A decorator on any of those four
    functions, or a statement beside the definition that rebinds its name
    (`complete_turn = wrap(complete_turn)`, a `globals()[...]` or attribute
    store, a `with ... as` target and a `match` case included), can wrap
    the whole call in a handler the body never shows, so each is refused
    unless answered for by name (none today); so is a decorator on a
    function defined inside one of the four, since a statement moved into
    it is wrapped in whatever the decorator likes. A `try` with no handler
    whose `finally` returns, breaks or continues at its own level discards
    the exception in flight, so it is read as a handler wherever a
    `try`/`except` is (none today: `gen()`'s own `finally` only persists).
    A rebinding made from another file or at run time (`sys.modules[...]`,
    `setattr`) is outside the rule: no read of the defining file sees it.

    The operator's frame set narrowed it further: `eval_metric` never
    arrived, so execution stopped between the lineage record and there. The
    two suspects this docstring named then were the per-turn curiosity call
    and `evaluator.evaluate`. WHICH ONE IS NOT ESTABLISHED HERE and must not
    be guessed; the traceback in the server console names it exactly. Since
    AX.1 the curiosity pass runs on the KAIROS cycle by default
    (`NEXUS_CURIOSITY_PLACEMENT=idle`), so on today's default path only
    `evaluate` and the calls before it remain there (the stated confidences
    and the boundary act, the act in its own `try`, both made before the held
    answer is released; the calibration accrual, guarded inside; the unit
    admission and the forecast score, each in its own `try`; the citation
    capture, off by default).

    The test below raises `sqlite3.OperationalError("database is locked")`
    from `evaluator.evaluate`, a call that touches no SQLite itself: it
    reproduces the symptom at a site inside the stretch, not the operator's
    exception at its origin. The lock is real contention the operator met:
    OQ-4 measured KAIROS holding the write lock ~15.4 s per cycle against a
    5 s `busy_timeout`, on the per-row commit path that Packet AA's batched
    pass replaced by default (`NEXUS_CONSOLIDATION_BATCH=on`).

STATUS: the last case is `xfail(strict)`. This file RECORDS the defect; it
does not fix it. Fixing it means deciding what the client is told when a turn
dies after its tokens, and that adds a member to the 21-event SSE contract —
a constitution-level change, and the operator's call. A synthetic `done` was
considered and rejected: it would report a turn as complete when it was not,
which is the class of defect this whole packet exists to end.
"""
from __future__ import annotations

import socket
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest

from backend.kb.granularity import Granularity
from backend.server import create_app, create_services

TOKEN = "stream-truncation-token"


class _Answering:
    async def stream_chat(self, model, messages, num_ctx=8192):
        for chunk in ("The QTc change ", "at 600mg ", "was 11.4ms."):
            yield chunk

    async def get_embedding(self, text): return [0.0] * 768
    async def list_models(self): return []
    async def health(self): return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _turn(monkeypatch, tmp_path, *, break_post_token: bool):
    """Run one real turn over a real socket. Returns (frames, lineage_rows).

    A LIVE SERVER, not TestClient. `TestClient(raise_server_exceptions=False)`
    returns an empty body for this case — it discards the partial response —
    so it would report zero frames for both arms and prove nothing. The
    truncation is a property of the wire.
    """
    import uvicorn
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    svc = create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "m.jsonl", archive_db_path=tmp_path / "a.db",
        snapshots_dir=tmp_path / "snap", provider=_Answering())
    svc.store.add(subject="qtc change at 600mg", relation="was",
                  object="11.4ms", weight=0.8, tier=1,
                  granularity=Granularity.TOPIC, source="human_assertion")

    if break_post_token:
        # The symptom's exception type, raised at a site inside the stretch
        # the operator's frame set implicates (THE WINDOW, above), not the
        # operator's exception at its origin. A locked database is the
        # contention the operator met: OQ-4's measurement of KAIROS holding
        # the write lock, on the per-row commit path that Packet AA's batched
        # pass replaced by default.
        def _locked(*a, **kw):
            raise sqlite3.OperationalError("database is locked")
        svc.evaluator.evaluate = _locked

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        create_app(svc), host="127.0.0.1", port=port, log_level="critical"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.05)
    assert server.started, "the test server never came up"

    events: list[str] = []
    status = None
    try:
        with httpx.Client(timeout=30) as client:
            with client.stream(
                "POST", f"http://127.0.0.1:{port}/chat",
                json={"message": "what was the QTc change at 600mg?"},
                headers={"Authorization": f"Bearer {TOKEN}"},
            ) as r:
                status = r.status_code
                buf = ""
                for chunk in r.iter_text():
                    buf += chunk
                    while "\n\n" in buf:
                        frame, buf = buf.split("\n\n", 1)
                        if frame.startswith("event: "):
                            events.append(frame.split("\n")[0][7:])
    except httpx.RemoteProtocolError:
        # The truncation itself. Recorded, not raised: it is the symptom.
        pass
    finally:
        server.should_exit = True

    rows = svc.store.conn.execute(
        "SELECT COUNT(*) n FROM lineage_records").fetchone()["n"]
    return events, rows, status


def test_a_healthy_turn_delivers_done(monkeypatch, tmp_path):
    """NON-VACUITY. Without this, "no done frame" below could mean the
    harness never gets one."""
    events, rows, status = _turn(monkeypatch, tmp_path, break_post_token=False)
    assert status == 200
    assert events[-1] == "done", f"a clean turn did not end with done: {events}"
    assert rows == 1


def test_a_post_token_exception_truncates_the_stream_after_token(monkeypatch,
                                                                 tmp_path):
    """THE OPERATOR'S FRAME SET, reproduced. This asserts the defect as it
    stands, so it passes today and fails the moment the behaviour changes —
    whoever changes it has to come here and say what it changed to."""
    events, rows, status = _turn(monkeypatch, tmp_path, break_post_token=True)

    assert status == 200, (
        "the failure is not visible in the status code, and that is the "
        "point: the response was committed before the exception happened")
    assert events[-1] == "token", (
        f"expected the stream to end on a token frame, got: {events}")
    for absent in ("done", "eval_metric", "prediction_error", "hebbian_update"):
        assert absent not in events, (
            f"{absent} arrived — the window this test describes has moved")
    assert rows == 1, (
        "the lineage record is missing, so this no longer reproduces the "
        "operator's report: the record existed while the frame did not")


@pytest.mark.xfail(strict=True, reason=(
    "OPEN DEFECT, recorded not fixed. /chat's stream generator (gen() in "
    "backend/server.py) is try/finally with no except, so a post-token "
    "exception ends the stream with no frame telling "
    "the client the turn failed. Fixing it adds a member to the 21-event SSE "
    "contract and needs a frontend handler, which is the operator's call. A "
    "synthetic `done` is NOT the fix — it would report a failed turn as "
    "complete."))
def test_the_client_is_told_the_turn_ended_abnormally(monkeypatch, tmp_path):
    """The property that should hold. I-6: a turn that dies after its tokens
    must say so, exactly as a KAIROS cycle that aborts now reports `None`
    rather than a plausible zero."""
    events, _rows, _status = _turn(monkeypatch, tmp_path, break_post_token=True)
    assert any(e in ("stream_error", "turn_failed") for e in events), (
        f"the stream ended silently after {events[-1]!r}; a client cannot "
        f"distinguish this from a turn that simply had nothing more to say")


# ---------------------------------------------------------------------------
# W1 — the docstring's claims, checked where they live
# ---------------------------------------------------------------------------

import ast  # noqa: E402
import io  # noqa: E402
import re  # noqa: E402
import tokenize  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# The files the docstring's construct claims are about.
WINDOW_SOURCES = {
    "server": ROOT / "backend" / "server.py",
    "loop": ROOT / "backend" / "loop" / "agentic_loop.py",
    "hebbian": ROOT / "backend" / "cortex" / "hebbian.py",
    "history": ROOT / "backend" / "loop" / "history.py",
    "ragas": ROOT / "backend" / "eval" / "ragas.py",
    "capture": ROOT / "backend" / "eval" / "citation_capture.py",
}
# A call that reaches SQLite by its own name: a connection or cursor method.
SQLITE_METHODS = {"execute", "executemany", "executescript", "commit", "cursor"}
# function -> the context expressions of the `with` statements in it that
# are answered for by name. A `with` is a handler the `try`-shaped rules do
# not see (the second verifier's F4: `with contextlib.suppress(Exception)`
# around `fire` left every "unguarded" claim reading as true), so in the
# functions whose claim is that nothing catches every other `with` is
# refused. A lock's exit suppresses nothing. A stale entry is a failure.
WITH_ANSWERED = {"gen": {"services.graph_lock"}}
# function -> the decorators on it that are answered for by name (the
# review's R3: a decorator wraps the whole call in whatever it likes, and
# the body never shows it). None today; a stale entry is a failure.
DECORATORS_ANSWERED: dict[str, set[str]] = {}
# The try statements that handle: `try`/`except` and, since Python 3.11,
# `try`/`except*` (`ast.TryStar`), which catches like `except` and which a
# rule reading `ast.Try` alone walked past (the review's R3).
TRY_NODES = tuple(getattr(ast, name) for name in ("Try", "TryStar") if hasattr(ast, name))


def _finally_swallows(t) -> bool:
    """Whether a try's `finally` discards the exception in flight: a
    `return`, `break` or `continue` at the finally's own level (the second
    verifier's S1: `try: ... finally: return` around the fire ended an
    async generator quietly). Descends into the finally's compound
    statements, never into a def, lambda or class (a `return` there is
    theirs), and not into a loop for `break`/`continue` (a loop's own)."""
    def walk(stmts, in_loop: bool) -> bool:
        for s in stmts:
            if isinstance(s, ast.Return) or (not in_loop and isinstance(s, (ast.Break, ast.Continue))):
                return True
            if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            loop = in_loop or isinstance(s, (ast.For, ast.AsyncFor, ast.While))
            for field in ("body", "orelse", "finalbody"):
                sub = getattr(s, field, None)
                if isinstance(sub, list) and walk(sub, loop):
                    return True
            for h in getattr(s, "handlers", []) or []:
                if walk(h.body, loop):
                    return True
            for c in getattr(s, "cases", []) or []:
                if walk(c.body, loop):
                    return True
        return False
    return walk(t.finalbody, False)


def _handles(t) -> bool:
    """A try that catches: it has handlers, or its finally swallows."""
    return isinstance(t, TRY_NODES) and (bool(t.handlers) or _finally_swallows(t))


def window_sources() -> dict:
    return {key: path.read_text(encoding="utf-8") for key, path in WINDOW_SOURCES.items()}


def _function(tree, name: str, cls: str | None = None):
    scope = tree
    if cls is not None:
        scope = next((c for c in ast.walk(tree) if isinstance(c, ast.ClassDef) and c.name == cls), None)
        if scope is None:
            return None
    return next((f for f in ast.walk(scope) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and f.name == name), None)


def _statements(fn) -> list:
    """A function's body without its docstring."""
    body = fn.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return body


def _calls(node, text: str) -> list:
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and ast.unparse(n.func) == text]


def _guarded(fn) -> dict:
    """id(node) -> the innermost try that catches (`try`/`except`,
    `try`/`except*`, or a `try` whose `finally` swallows) whose BODY holds
    the node. Only a try's body is guarded by its handlers, never its
    handlers, else or finally."""
    out = {}
    for t in ast.walk(fn):
        if _handles(t):
            for stmt in t.body:
                for n in ast.walk(stmt):
                    out[id(n)] = t
    return out


def _sqlite_shaped(call: ast.Call) -> bool:
    func = ast.unparse(call.func)
    return (isinstance(call.func, ast.Attribute) and call.func.attr in SQLITE_METHODS) \
        or ".conn." in f"{func}." or func.startswith("sqlite3.")


def _unread_withs(fn, name: str) -> list[str]:
    """Every `with` in `fn` whose context expression WITH_ANSWERED does not
    name, and every answered expression no `with` in `fn` carries."""
    answered = WITH_ANSWERED.get(name, set())
    found = [ast.unparse(item.context_expr) for w in ast.walk(fn)
             if isinstance(w, (ast.With, ast.AsyncWith)) for item in w.items]
    return ([f"{name} holds a `with` this check cannot read as a handler or not: {ctx}"
             for ctx in found if ctx not in answered]
            + [f"the answered `with {ctx}` in {name} is gone" for ctx in sorted(answered) if ctx not in found])


def _bound_names(stmt) -> set[str]:
    """The names a statement binds at its own level: assignment targets
    (flattened through tuples and lists; an attribute target by its
    attribute, `mod.complete_turn = ...`; a subscript target by its string
    key, `globals()["complete_turn"] = ...`), a `with ... as` target, a def
    or class name, an import's bound name."""
    out: set[str] = set()
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        out.add(stmt.name)
    elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
        out |= {(a.asname or a.name).split(".")[0] for a in stmt.names}
    targets = list(getattr(stmt, "targets", [])) + ([stmt.target] if hasattr(stmt, "target") else [])
    targets += [item.optional_vars for item in getattr(stmt, "items", []) or [] if item.optional_vars is not None]
    while targets:
        t = targets.pop()
        if isinstance(t, (ast.Tuple, ast.List)):
            targets += t.elts
        elif isinstance(t, ast.Name):
            out.add(t.id)
        elif isinstance(t, ast.Attribute):
            out.add(t.attr)
        elif isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant) and isinstance(t.slice.value, str):
            out.add(t.slice.value)
    return out


def _rebinders(body: list, fn, name: str) -> list:
    """Every statement in `body` (descending into compound statements, a
    `match`'s cases included, never into another def or class) other than
    `fn` itself that binds `name`."""
    out = []
    for s in body:
        if s is fn:
            continue
        if name in _bound_names(s):
            out.append(s)
        if isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        for field in ("body", "orelse", "finalbody", "handlers", "cases"):
            sub = getattr(s, field, None)
            if isinstance(sub, list):
                out += _rebinders(sub, fn, name)
    return out


def _unread_wrappers(fn, name: str, enclosing: list) -> list[str]:
    """What can wrap the whole of `fn`, or a part of it, in a handler its
    body never shows (the review's R3): a decorator DECORATORS_ANSWERED does
    not name, an answered decorator the function no longer carries, a
    statement in the enclosing body — the module's, or `chat`'s for `gen()`
    — that rebinds the name beside its definition (`complete_turn =
    wrap(complete_turn)`), and a decorator on a function defined inside
    `fn` (the second verifier's S2: a statement moved into a decorated
    nested def is wrapped in whatever the decorator likes). A nested def's
    decorator is answered for under `<name>.<inner>`."""
    answered = DECORATORS_ANSWERED.get(name, set())
    found = [ast.unparse(d) for d in fn.decorator_list]
    nested = [(inner, ast.unparse(d)) for inner in ast.walk(fn)
              if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)) and inner is not fn
              for d in inner.decorator_list]
    return ([f"{name} carries a decorator this check cannot read as a handler or not: @{d}"
             for d in found if d not in answered]
            + [f"the answered decorator @{d} on {name} is gone" for d in sorted(answered) if d not in found]
            + [f"{name} holds a decorated nested def this check cannot read as a handler or not: "
               f"@{d} on {inner.name}" for inner, d in nested
               if d not in DECORATORS_ANSWERED.get(f"{name}.{inner.name}", set())]
            + [f"{name} is rebound beside its definition, which this check cannot read: {ast.unparse(s)[:60]}"
               for s in _rebinders(enclosing, fn, name)])


def window_claims(sources: dict) -> list[str]:
    """Every claim the docstring's THE WINDOW section makes about a construct,
    as a list of the ones that no longer hold (empty: all hold). `sources`
    maps `WINDOW_SOURCES`'s keys to source text, so a companion can plant a
    change in one file and leave the others as they are. Calls are placed
    by line order (the docstring says so beside the SQL-trace caveat), and
    the constructs that order cannot read — a `with`, a decorator on one of
    the four functions or on a def nested in them, a rebinding of the name,
    and a function defined inside `complete_turn` above the record — are
    refused rather than read past; a `try`/`except*`, and a `try` whose
    `finally` swallows, are read as the handlers they are."""
    problems = []
    server = ast.parse(sources["server"])
    chat = next((f for f in ast.walk(server) if isinstance(f, ast.AsyncFunctionDef)
                 and f.name == "chat"), None)
    gen = next((f for f in ast.walk(chat) if isinstance(f, ast.AsyncFunctionDef)
                and f.name == "gen"), None) if chat else None
    if gen is None:
        return ["/chat's stream generator gen() was not found"]
    wrapping = [t for t in ast.walk(gen) if isinstance(t, TRY_NODES)
                and any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "run_turn"
                        for n in ast.walk(t))]
    if not wrapping:
        problems.append("gen() no longer runs run_turn inside a try")
    elif any(_handles(t) for t in wrapping) or not wrapping[0].finalbody:
        problems.append("gen()'s try around run_turn now catches, or has no finally")
    problems += _unread_withs(gen, "gen") + _unread_wrappers(gen, "gen", chat.body)

    loop = ast.parse(sources["loop"])
    fns = {f.name: f for f in loop.body if isinstance(f, ast.AsyncFunctionDef)}
    if any(isinstance(n, TRY_NODES) for n in ast.walk(fns["run_turn"])):
        problems.append("run_turn now has a try")
    problems += _unread_withs(fns["run_turn"], "run_turn") + _unread_wrappers(fns["run_turn"], "run_turn", loop.body)
    complete = fns["complete_turn"]
    problems += _unread_withs(complete, "complete_turn") + _unread_wrappers(complete, "complete_turn", loop.body)
    guarded = _guarded(complete)

    def emits(event):
        return [n for n in ast.walk(complete) if isinstance(n, ast.Call)
                and getattr(n.func, "id", "") == "sse_event" and n.args
                and isinstance(n.args[0], ast.Constant) and n.args[0].value == event]

    record = _calls(complete, "lineage.record_turn")
    if len(record) != 1 or id(record[0]) not in guarded:
        problems.append("the record is no longer written once inside its own try/except")
        return problems
    after_line = record[0].lineno
    # The line-order read cannot place a function defined above the record
    # and called below it (the second verifier's N5), so none may exist.
    above = [getattr(n, "name", "<lambda>") for n in ast.walk(complete)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
             and n is not complete and n.lineno < after_line]
    if above:
        problems.append(f"complete_turn defines a function above the record, which the line-order "
                        f"read cannot place: {above}")
    after = {"evaluator.evaluate": _calls(complete, "evaluator.evaluate"),
             "eval_metric": emits("eval_metric"),
             "prediction_error": emits("prediction_error"),
             "hebbian_update": emits("hebbian_update"),
             "retriever.store.fire": _calls(complete, "retriever.store.fire"),
             "done": emits("done")}
    for name, found in after.items():
        if not found or min(c.lineno for c in found) < after_line:
            problems.append(f"{name} no longer follows the record")

    # The handlers: each guards one write. Before the record, the
    # extraction's add() calls; after it, only the boundary act (W6: made
    # after the record, declared `write_failed` when it cannot be), the
    # admission and the score.
    trys = {id(t): t for t in ast.walk(complete) if _handles(t)}
    later = [t for t in trys.values() if t.lineno > after_line]
    act = [c for c in _calls(complete, "work_units.apply_boundary_for_turn") if id(c) in guarded]
    admit = [c for c in _calls(complete, "work_units.admit") if id(c) in guarded]
    score = [c for c in _calls(complete, "score_forecast") if id(c) in guarded]
    if not act or not admit or not score or len({id(guarded[id(c[0])]) for c in (act, admit, score)}) != 3:
        problems.append("the boundary act, the unit admission and the forecast score are no longer each in its own try")
    if len(later) != 3:
        problems.append(f"complete_turn now has {len(later)} handled trys after the record, "
                        f"not the boundary act's, the admission's and the score's")
    earlier = [t for t in trys.values() if t.lineno < after_line and t is not guarded[id(record[0])]]
    if not earlier or not all(_calls(t, "retriever.store.add") for t in earlier):
        problems.append("a handled try before the record no longer guards the extraction's add()")

    # The one unguarded call that reaches SQLite by its own name.
    fire = after["retriever.store.fire"]
    if fire and id(fire[0]) in guarded:
        problems.append("retriever.store.fire is now guarded")
    unguarded_sql = sorted({ast.unparse(n.func) for n in ast.walk(complete)
                            if isinstance(n, ast.Call) and n.lineno > after_line
                            and id(n) not in guarded and _sqlite_shaped(n)})
    if unguarded_sql:
        problems.append(f"complete_turn calls SQLite unguarded after the record: {unguarded_sql}")
    unguarded_store = sorted({ast.unparse(n.func) for n in ast.walk(complete)
                              if isinstance(n, ast.Call) and n.lineno > after_line
                              and id(n) not in guarded and ".store." in ast.unparse(n.func)})
    if unguarded_store != ["retriever.store.fire"]:
        problems.append(f"the unguarded store calls after the record are {unguarded_store}, "
                        f"not retriever.store.fire alone")

    # The calibration accrual: called after the record, guarded inside.
    if not [c for c in _calls(complete, "_accrue_calibration") if c.lineno > after_line]:
        problems.append("_accrue_calibration is no longer called after the record")
    accrue = _function(loop, "_accrue_calibration")
    if accrue is None or not [c for c in _calls(accrue, "_log_confidence") if id(c) in _guarded(accrue)]:
        problems.append("the calibration accrual's write is no longer guarded inside _accrue_calibration")

    # The citation capture: off unless switched on, and its write guarded.
    capture = _function(ast.parse(sources["capture"]), "capture_turn")
    first = (_statements(capture) or [None])[0] if capture is not None else None
    if capture is None or not (isinstance(first, ast.If) and "capture_enabled()" in ast.unparse(first.test)
                               and any(isinstance(s, ast.Return) for s in first.body)):
        problems.append("capture_turn no longer returns before anything when the capture is off")
    elif not any(isinstance(t, TRY_NODES) and t.handlers for t in ast.walk(capture)):
        problems.append("capture_turn's write is no longer inside a handled try")

    # hebbian.update contains the failure of the SQLite read under it.
    hebbian = ast.parse(sources["hebbian"])
    update, protected = _function(hebbian, "update", "HebbianUpdater"), _function(hebbian, "_protected", "HebbianUpdater")
    contains = update is not None and any(
        isinstance(t, TRY_NODES) and _calls(ast.Module(body=t.body, type_ignores=[]), "self._protected")
        and any(h.type is not None and ast.unparse(h.type) == "ProtectionUnavailable" for h in t.handlers)
        for t in ast.walk(update))
    converts = protected is not None and any(
        isinstance(t, TRY_NODES) and any(h.type is not None and ast.unparse(h.type) == "Exception"
                                         and any(isinstance(s, ast.Raise) and "ProtectionUnavailable" in ast.unparse(s)
                                                 for s in h.body) for h in t.handlers)
        for t in ast.walk(protected))
    if not (contains and converts):
        problems.append("hebbian.update no longer contains the failure of its protected-pair read")

    # CarrierMismatch: raised by align_carrier, which complete_turn calls unguarded.
    history = ast.parse(sources["history"])
    align = _function(history, "align_carrier")
    if align is None or not any(isinstance(n, ast.Raise) and "CarrierMismatch" in ast.unparse(n) for n in ast.walk(align)):
        problems.append("align_carrier no longer raises CarrierMismatch")
    if align is not None:
        problems += _unread_withs(align, "align_carrier") + _unread_wrappers(align, "align_carrier", history.body)
    if not [c for c in _calls(complete, "align_carrier") if id(c) not in guarded]:
        problems.append("complete_turn no longer calls align_carrier unguarded")

    # evaluator.evaluate touches no SQLite itself.
    evaluate = _function(ast.parse(sources["ragas"]), "evaluate", "TurnEvaluator")
    if evaluate is None:
        problems.append("TurnEvaluator.evaluate was not found")
    elif any(isinstance(n, ast.Call) and _sqlite_shaped(n) for n in ast.walk(evaluate)) \
            or any(isinstance(n, ast.Name) and n.id == "sqlite3" for n in ast.walk(evaluate)):
        problems.append("TurnEvaluator.evaluate now touches SQLite")
    # The curiosity claim (nothing under backend/loop calls find_curious_nodes)
    # is held by tests/test_ax_curiosity_placement.py's derived guard.
    return problems


def test_the_window_is_where_this_says(monkeypatch):
    """W1 — every line number this docstring carried was stale (all ten), so
    it names constructs now, and this checks each claim about a construct by
    AST. W1's verifier (F2) found five of the docstring's claims checked and
    the rest not; the rest are checked now, each with a companion below."""
    assert window_claims(window_sources()) == []
    # "the citation capture, off by default", read the way the turn reads it.
    from backend.eval.citation_capture import capture_enabled
    monkeypatch.delenv("NEXUS_CITATION_CAPTURE", raising=False)
    assert capture_enabled() is False


def _rewrite(source: str, change) -> str:
    tree = ast.parse(source)
    change(tree)
    return ast.unparse(ast.fix_missing_locations(tree))


def _unwrap_try(fn_name: str, call: str, cls: str | None = None):
    """Replace the handled try that guards `call` in `fn_name` by its body."""
    def change(tree):
        fn = _function(tree, fn_name, cls)
        for parent in ast.walk(fn):
            for field in ("body", "orelse", "finalbody"):
                stmts = getattr(parent, field, None)
                if not isinstance(stmts, list):
                    continue
                for i, s in enumerate(stmts):
                    if isinstance(s, TRY_NODES) and s.handlers and _calls(ast.Module(body=s.body, type_ignores=[]), call):
                        stmts[i:i + 1] = s.body
                        return
        raise AssertionError(f"no handled try around {call} in {fn_name}")
    return change


def _wrap_in_try(fn_name: str, call: str, star: bool = False):
    """Put the top-level statement of `fn_name` that holds `call` in a handled
    try — `try`/`except`, or `try`/`except*` (`ast.TryStar`) when `star`."""
    def change(tree):
        fn = _function(tree, fn_name)
        for i, s in enumerate(fn.body):
            if _calls(s, call):
                kind = ast.TryStar if star else ast.Try
                fn.body[i] = kind(body=[s], handlers=[ast.ExceptHandler(
                    type=ast.Name(id="Exception", ctx=ast.Load()), name=None, body=[ast.Pass()])],
                    orelse=[], finalbody=[])
                return
        raise AssertionError(f"{call} not found at the top of {fn_name}")
    return change


def _decorate(fn_name: str, decorator: str):
    """Put `@<decorator>` on `fn_name`."""
    def change(tree):
        _function(tree, fn_name).decorator_list.append(ast.parse(decorator).body[0].value)
    return change


def _wrap_in_finally_return(fn_name: str, call: str):
    """Put the top-level statement of `fn_name` that holds `call` in a
    `try` with no handler whose `finally` returns: no `except`, and the
    exception in flight is discarded all the same."""
    def change(tree):
        fn = _function(tree, fn_name)
        for i, s in enumerate(fn.body):
            if _calls(s, call):
                fn.body[i] = ast.Try(body=[s], handlers=[], orelse=[], finalbody=[ast.Return(value=None)])
                return
        raise AssertionError(f"{call} not found at the top of {fn_name}")
    return change


def _return_in_finally(fn_name: str, call: str):
    """Append a bare `return` to the `finally` of the try in `fn_name` that
    holds `call` (gen()'s own try around run_turn)."""
    def change(tree):
        fn = _function(tree, fn_name)
        for t in ast.walk(fn):
            if isinstance(t, TRY_NODES) and _calls(ast.Module(body=t.body, type_ignores=[]), call):
                t.finalbody.append(ast.Return(value=None))
                return
        raise AssertionError(f"no try around {call} in {fn_name}")
    return change


def _nest_in_decorated_def(fn_name: str, call: str, decorator: str):
    """Move the top-level statement of `fn_name` that holds `call` into a
    nested def carrying `@<decorator>`, called where the statement stood."""
    def change(tree):
        fn = _function(tree, fn_name)
        for i, s in enumerate(fn.body):
            if _calls(s, call):
                inner = ast.FunctionDef(name="_moved", args=ast.arguments(
                    posonlyargs=[], args=[], vararg=None, kwonlyargs=[], kw_defaults=[], kwarg=None,
                    defaults=[]), body=[s], decorator_list=[ast.parse(decorator).body[0].value],
                    returns=None, type_comment=None)
                fn.body[i:i + 1] = [inner, ast.Expr(value=ast.Call(
                    func=ast.Name(id="_moved", ctx=ast.Load()), args=[], keywords=[]))]
                return
        raise AssertionError(f"{call} not found at the top of {fn_name}")
    return change


def _rebind(fn_name: str, template: str, enclosing: str | None = None):
    """Insert the statement `template` (with `{name}` filled in, e.g.
    `"{name} = _swallow({name})"`) right after the definition, in the body
    that holds it: the module's, or the body of the function `enclosing`."""
    def change(tree):
        body = tree.body if enclosing is None else _function(tree, enclosing).body
        fn = _function(tree, fn_name)
        body.insert(body.index(fn) + 1, ast.parse(template.format(name=fn_name)).body[0])
    return change


def _wrap_in_with(fn_name: str, call: str, manager: str = "contextlib.suppress(Exception)"):
    """Put the top-level statement of `fn_name` that holds `call` under
    `with <manager>:` — a handler with no `try` in it."""
    def change(tree):
        fn = _function(tree, fn_name)
        for i, s in enumerate(fn.body):
            if _calls(s, call):
                fn.body[i] = ast.With(items=[ast.withitem(context_expr=ast.parse(manager).body[0].value,
                                                          optional_vars=None)], body=[s])
                return
        raise AssertionError(f"{call} not found at the top of {fn_name}")
    return change


def _unwrap_with(fn_name: str, manager: str):
    """Replace the `with <manager>:` in `fn_name` by its body."""
    def change(tree):
        fn = _function(tree, fn_name)
        for parent in ast.walk(fn):
            for field in ("body", "orelse", "finalbody"):
                stmts = getattr(parent, field, None)
                if not isinstance(stmts, list):
                    continue
                for i, s in enumerate(stmts):
                    if isinstance(s, (ast.With, ast.AsyncWith)) \
                            and any(ast.unparse(item.context_expr) == manager for item in s.items):
                        stmts[i:i + 1] = s.body
                        return
        raise AssertionError(f"no `with {manager}` in {fn_name}")
    return change


def _insert_before(fn_name: str, call: str, statement: str, cls: str | None = None):
    """Insert `statement` before the top-level statement of `fn_name` that holds `call`."""
    def change(tree):
        fn = _function(tree, fn_name, cls)
        for i, s in enumerate(fn.body):
            if call is None or _calls(s, call):
                fn.body[i:i] = ast.parse(statement).body
                return
        raise AssertionError(f"{call} not found at the top of {fn_name}")
    return change


def _drop_raises(fn_name: str, name: str):
    def change(tree):
        fn = _function(tree, fn_name)
        for parent in ast.walk(fn):
            for field in ("body", "orelse"):
                stmts = getattr(parent, field, None)
                if isinstance(stmts, list):
                    stmts[:] = [ast.Pass() if isinstance(s, ast.Raise) and name in ast.unparse(s) else s
                                for s in stmts]
    return change


# Each claim, broken in memory in the file it is about; the check must name
# it — and name nothing else: the problems are exactly the ones listed, one
# each (the second verifier's N8). Three plants break two claims and list
# both: an unwrapped handler both unpairs the admission and the score and
# changes the count of handlers after the record, and a planted store call
# is both an unguarded SQLite call and a second store call.
# (The except planted into gen() is the first, kept in its own test below.)
WINDOW_COMPANIONS = {
    "admission unguarded": ("loop", _unwrap_try("complete_turn", "work_units.admit"),
                            ("each in its own try", "handled trys after the record")),
    "forecast score unguarded": ("loop", _unwrap_try("complete_turn", "score_forecast"),
                                 ("each in its own try", "handled trys after the record")),
    "a new handler after the record": ("loop", _wrap_in_try("complete_turn", "evaluator.evaluate"),
                                       ("handled trys after the record",)),
    "an unguarded SQLite call after the record": (
        "loop", _insert_before("complete_turn", "capture_turn", "retriever.store.conn.execute('SELECT 1')"),
        ("calls SQLite unguarded after the record", "not retriever.store.fire alone")),
    "a second unguarded store call": (
        "loop", _insert_before("complete_turn", "capture_turn", "retriever.store.count()"),
        ("not retriever.store.fire alone",)),
    "the accrual's write unguarded": ("loop", _unwrap_try("_accrue_calibration", "_log_confidence"),
                                      ("no longer guarded inside _accrue_calibration",)),
    "the capture on regardless of its switch": (
        "capture", lambda tree: _function(tree, "capture_turn").body.remove(
            _statements(_function(tree, "capture_turn"))[0]),
        ("no longer returns before anything when the capture is off",)),
    "hebbian lets the protection failure out": (
        "hebbian", _unwrap_try("update", "self._protected", "HebbianUpdater"),
        ("no longer contains the failure",)),
    "align_carrier raises nothing": ("history", _drop_raises("align_carrier", "CarrierMismatch"),
                                     ("no longer raises CarrierMismatch",)),
    "evaluate reads the store": (
        "ragas", _insert_before("evaluate", None, "self.conn.execute('SELECT 1')", "TurnEvaluator"),
        ("now touches SQLite",)),
    # The second verifier's F4: a `with` is a handler the try-shaped rules
    # cannot see. Planted around the fire and around align_carrier in
    # complete_turn, in run_turn, inside align_carrier, and in gen() under a
    # manager the allowance does not name; and the allowance's own witness,
    # the lock's `with` removed from gen().
    "a `with` around the fire": ("loop", _wrap_in_with("complete_turn", "retriever.store.fire"),
                                 ("complete_turn holds a `with` this check cannot read",)),
    "a `with` around align_carrier": ("loop", _wrap_in_with("complete_turn", "align_carrier"),
                                      ("complete_turn holds a `with` this check cannot read",)),
    "a `with` in run_turn": ("loop", _wrap_in_with("run_turn", "complete_turn"),
                             ("run_turn holds a `with` this check cannot read",)),
    "a `with` inside align_carrier": ("history", _wrap_in_with("align_carrier", "WRAPPER_RE.match"),
                                      ("align_carrier holds a `with` this check cannot read",)),
    "a `with` in gen that is not the lock": ("server", _wrap_in_with("gen", "run_turn", "_swallow()"),
                                             ("gen holds a `with` this check cannot read",)),
    "the lock's `with` gone from gen": ("server", _unwrap_with("gen", "services.graph_lock"),
                                        ("the answered `with services.graph_lock` in gen is gone",)),
    # The second verifier's N5: a helper defined above the record and called
    # below it, which the line-order read cannot place.
    "a helper defined above the record": (
        "loop", _insert_before("complete_turn", "lineage.record_turn",
                               "def _late():\n    retriever.store.conn.execute('SELECT 1')"),
        ("defines a function above the record",)),
    # The review's R3: `try`/`except*` around the fire is a handler like any
    # other (it guards the fire, is one more handled try after the record,
    # and leaves no unguarded store call); around align_carrier it is one
    # more handled try and the carrier call is no longer unguarded; in
    # run_turn it is the try run_turn must not have; around gen()'s own
    # try it catches. A decorator on each of the four functions. The name
    # rebound beside its definition: plainly, through `globals()`, and
    # inside `chat` for gen().
    "an except* around the fire": ("loop", _wrap_in_try("complete_turn", "retriever.store.fire", star=True),
                                   ("retriever.store.fire is now guarded", "handled trys after the record",
                                    "not retriever.store.fire alone")),
    "an except* around align_carrier": ("loop", _wrap_in_try("complete_turn", "align_carrier", star=True),
                                        ("handled trys after the record", "no longer calls align_carrier unguarded")),
    "an except* in run_turn": ("loop", _wrap_in_try("run_turn", "complete_turn", star=True),
                               ("run_turn now has a try",)),
    "an except* around gen's try": ("server", _wrap_in_try("gen", "run_turn", star=True),
                                    ("now catches",)),
    "a decorator on complete_turn": ("loop", _decorate("complete_turn", "_swallow_everything"),
                                     ("complete_turn carries a decorator this check cannot read",)),
    "a decorator on run_turn": ("loop", _decorate("run_turn", "_swallow_everything"),
                                ("run_turn carries a decorator this check cannot read",)),
    "a decorator on align_carrier": ("history", _decorate("align_carrier", "functools.wraps(_quiet)"),
                                     ("align_carrier carries a decorator this check cannot read",)),
    "a decorator on gen": ("server", _decorate("gen", "_swallow_everything"),
                           ("gen carries a decorator this check cannot read",)),
    "complete_turn rebound at module level": ("loop", _rebind("complete_turn", "{name} = _swallow({name})"),
                                              ("complete_turn is rebound beside its definition",)),
    "complete_turn rebound through globals()": (
        "loop", _rebind("complete_turn", "globals()['{name}'] = _swallow({name})"),
        ("complete_turn is rebound beside its definition",)),
    "align_carrier rebound at module level": ("history", _rebind("align_carrier", "{name} = _swallow({name})"),
                                              ("align_carrier is rebound beside its definition",)),
    "gen rebound inside chat": ("server", _rebind("gen", "{name} = _swallow({name})", enclosing="chat"),
                                ("gen is rebound beside its definition",)),
    # The second verifier's S1 (on part 5h): a `try` with no handler whose
    # `finally` returns discards the exception in flight, so around the fire
    # it is a handler like any other (three claims), around align_carrier
    # two, and in gen()'s own `finally` it catches. S2: a statement moved
    # into a decorated nested def below the record is wrapped in whatever
    # the decorator likes, while every line-order claim still reads as
    # holding. Its N1: the two rebinding shapes the first rule skipped.
    "a finally that returns around the fire": (
        "loop", _wrap_in_finally_return("complete_turn", "retriever.store.fire"),
        ("retriever.store.fire is now guarded", "handled trys after the record",
         "not retriever.store.fire alone")),
    "a finally that returns around align_carrier": (
        "loop", _wrap_in_finally_return("complete_turn", "align_carrier"),
        ("handled trys after the record", "no longer calls align_carrier unguarded")),
    "a return in gen's finally": ("server", _return_in_finally("gen", "run_turn"), ("now catches",)),
    "a decorated nested def holding the fire": (
        "loop", _nest_in_decorated_def("complete_turn", "retriever.store.fire", "_swallow_everything"),
        ("complete_turn holds a decorated nested def this check cannot read",)),
    "a decorated nested def holding align_carrier": (
        "loop", _nest_in_decorated_def("complete_turn", "align_carrier", "contextlib.suppress(Exception)"),
        ("complete_turn holds a decorated nested def this check cannot read",)),
    "complete_turn rebound through a with target": (
        "loop", _rebind("complete_turn", "with _swallowed({name}) as {name}:\n    pass"),
        ("complete_turn is rebound beside its definition",)),
    "complete_turn rebound inside a match case": (
        "loop", _rebind("complete_turn", "match 1:\n    case _:\n        {name} = _swallow({name})"),
        ("complete_turn is rebound beside its definition",)),
}


@pytest.mark.parametrize("name", sorted(WINDOW_COMPANIONS))
def test_a_broken_window_claim_is_named(name):
    """COMPANIONS (OQ-16 rules 1 and 3): each claim of THE WINDOW, broken in
    the file it is about, is named by the check, and nothing else is — the
    problems are exactly the ones listed for the plant, one each (the
    second verifier's N8: the first version asserted presence and wrote
    'the rest still hold' without checking it)."""
    key, change, expected = WINDOW_COMPANIONS[name]
    sources = window_sources()
    planted = dict(sources, **{key: _rewrite(sources[key], change)})
    problems = window_claims(planted)
    assert len(problems) == len(expected), (name, problems)
    for e in expected:
        assert sum(e in p for p in problems) == 1, (name, e, problems)


def test_the_window_check_names_a_planted_except():
    """Companion: an `except` in gen() is the fix this file waits for, and the
    claim check must notice it rather than keep describing a window that has
    closed."""
    sources = window_sources()
    tree = ast.parse(sources["server"])
    gen = next(f for f in ast.walk(tree) if isinstance(f, ast.AsyncFunctionDef) and f.name == "gen")
    t = next(t for t in ast.walk(gen) if isinstance(t, ast.Try))
    t.handlers.append(ast.ExceptHandler(type=ast.Name(id="Exception", ctx=ast.Load()),
                                        name=None, body=[ast.Pass()]))
    problems = window_claims(dict(sources, server=ast.unparse(tree)))
    assert any("now catches" in p for p in problems), problems


# The class a line citation belongs to (W1; widened after W1's verifier, F1,
# planted eleven forms the first pattern missed, and after the second
# verifier's F6 and N1): a file name with an extension and a line, plain or
# in backticks; a GitHub line anchor; "line" or "lines" and a number; a bare
# anchor of two or more digits; and a parenthesised number of two to five
# digits — a YEAR INCLUDED. The first version excluded a parenthesised 19xx
# or 20xx, which un-guarded every parenthesised line number from 1900 to
# 2099 (the range server.py's lines fall in) to protect a form this file
# has never held; a year in prose is written in a date, "(1 November 2026)"
# or "(since 2026)", a form the class does not match. The name member (N1):
# a stem of word characters, dots, slashes and dashes — empty for a dotfile
# such as `.gitignore` — then an extension of one to nine letters or digits
# with at least one letter (`server.config`, `run.py3`; never a decimal such
# as `0.35`), an optional closing backtick, a colon and the digits. Outside
# the class by its shape, recorded: a name that is a formatted value
# (f"{path}" before the colon), a space after the colon, a space inside the
# parentheses ("( 1973)"), an anchor on a name with no extension (a
# Makefile), and "l." for line.
LINE_CITATION = re.compile(r"""
      [\w./-]*\.(?=[A-Za-z0-9]{0,8}[A-Za-z])[A-Za-z0-9]{1,9}`?:\d+
    | [\w./-]*\.(?=[A-Za-z0-9]{0,8}[A-Za-z])[A-Za-z0-9]{1,9}\#L\d+
    | \blines?\s+\d+
    | \bL\d{2,}\b
    | \(\d{2,5}\)
""", re.VERBOSE)


def comments(source: str) -> list[str]:
    """Every comment in a file, which `ast` does not keep."""
    return [t.string for t in tokenize.generate_tokens(io.StringIO(source).readline)
            if t.type == tokenize.COMMENT]


def line_citations(source: str) -> list[str]:
    """Every line citation in a file's prose: its string constants, read by
    AST, and its comments. Nothing is excluded from the class."""
    prose = [n.value for n in ast.walk(ast.parse(source))
             if isinstance(n, ast.Constant) and isinstance(n.value, str)] + comments(source)
    return [m.group(0) for text in prose for m in LINE_CITATION.finditer(text)]


def test_this_files_prose_cites_constructs_not_lines():
    """W1 — a line number in prose goes stale silently (all ten here did);
    a construct's name fails the claim check above when it moves. Comments
    are prose too."""
    assert line_citations(Path(__file__).read_text(encoding="utf-8")) == []


# Built by concatenation, so that no constant in this file is itself a
# citation (the guard above reads every string constant and comment here).
# A name is split AFTER its colon, never before its dot: the class accepts a
# dotfile, so a half such as `.py` followed by the colon and digits would
# itself be a citation (this file's own guard caught the first split).
# The first three are the guard's first companions. Then the forms W1's
# verifier planted (F1), which the first pattern missed, and three of the
# same class that neither names: a .rst and a .toml file, and a three-digit
# parenthesised line. Last, the second verifier's: a parenthesised line in
# the range the year exclusion hid (F6), and the dotfile, long-extension and
# digit-bearing-extension names the first member's bound left out (N1).
PLANTED_CITATIONS = [
    '"""See server.py:' + '1973."""',
    '"""the token yield (' + '645)"""',
    '"""nexus_ui.html:' + '1195 renders it"""',
    'x = 1  # the done yield is agentic_loop.py:' + '1475\n',
    '"""line ' + '1973 of server.py"""',
    '"""server.py line ' + '1973"""',
    '"""CLAUDE.md:' + '1144"""',
    '"""nexus_ui.js:' + '88 and NOTES.md:' + '12"""',
    '"""backend/server.py#L' + '1973"""',
    '"""lines ' + '645-964"""',
    '"""the yield (' + '85)"""',
    '"""the yield (' + '12045)"""',
    '"""see L' + '1973"""',
    '"""`server.py`' + ':1973"""',
    '"""docs/RUNBOOK.rst:' + '40"""',
    '"""pyproject.toml:' + '7 pins it"""',
    'y = 2  # the record (' + '321) is written first\n',
    '"""the token yield (' + '1973)"""',
    '"""see .gitignore' + ':14"""',
    '"""server.config:' + '7 sets it"""',
    '"""run.py3:' + '12"""',
]


@pytest.mark.parametrize("planted", PLANTED_CITATIONS)
def test_a_planted_line_citation_is_named(planted):
    assert line_citations(planted), planted


def test_a_year_in_a_date_is_not_a_citation():
    """A year written in a date — the form prose takes — is outside the
    class; a bare parenthesised year is inside it and planted above (the
    second verifier's F6: the first version's exclusion of a parenthesised
    19xx or 20xx hid every parenthesised line number from 1900 to 2099,
    while the file held no bare year to protect)."""
    assert line_citations('"""measured (1 November ' + '2026)"""') == []
    assert line_citations('"""frozen (since ' + '2026)"""') == []
