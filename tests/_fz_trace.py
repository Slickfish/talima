"""FZ-2 — the write trace and the derived drivers (a tracked helper).

THE GUARD READS THE DATABASE, NOT A LIST OF PATHS. `WriteTrace` listens on
the store interface's trace hook (`backend.store`, W3b: every statement any
`Database` runs, on any backend; before W3b, SQLite's `set_trace_callback`
on every connection the application opened) and records each
INSERT/UPDATE/DELETE with the chain of backend frames that issued it. `pairs()` normalises every write to (table, host, method):

    host    boot | turn | cycle | route:<endpoint> | script:<module>
    method  the innermost backend frame that issued the SQL (module.function)

Intermediate helpers are dropped, so a helper rename changes nothing while
a new host or a new method is a new pair. A table-level view exists only so
T1c can show why it is not enough: `triples` must stay writable for the
claim path under frozen, so a sixth writer to `triples` passes a table
check and fails the pair check.

A FRAME IS NAMED BY ITS MODULE, NEVER BY ITS FILE PATH. The first cut
recognised a backend frame by the substring `/backend/` in the filename
`traceback.extract_stack()` rendered, and on the operating instance
(Windows) every filename carries backslashes, so no frame matched, every
chain was empty, every write read `("<table>", "other:?", "?")`, and the
seven chain-dependent tests failed while the seven that read no chain
passed. The trace never saw the defect because a sandbox is not the
operator's machine. `frame_tag` now reads `frame.f_globals["__name__"]` —
`backend.kb.tiered_store` is the same string on every platform — and the
one place a path is still consulted (a script run as `__main__`, which has
no dotted name) goes through pathlib in the flavour the string was written
in. `tests/test_fz_freeze.py` T11 feeds `chain_of` a synthetic frame whose
filename is a Windows path and reads the pair back.

THE DRIVERS ARE DERIVED, NOT LISTED: boot (the app is created inside the
trace), turns and forced cycles, every non-GET route from the app's own
route table, and every tracked script that imports a store module (found by
AST) run in its dry mode where one exists. Routes and scripts that cannot
be driven are named exemptions with a reason, printed. The residual is a
writer driven by none of those five, and that sentence is on the register.
"""
from __future__ import annotations

import ast
import collections
import contextlib
import io
import json
import os
import re
import runpy
import sys
import tempfile
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

from tests._tracked import REPO, tracked

_WRITE = re.compile(
    r"^\s*(INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO)\s+([`\"]?)(\w+)", re.I)
_SET_COLS = re.compile(r"\bSET\s+(.*?)\s+WHERE\b", re.I | re.S)

# The five learning methods as the pairs they produce. The neocortex
# promotion entry produces its writes through `add()` under the cycle host,
# which is how it differs from extraction's `add()` under the turn host.
LEARNING_PAIRS: dict[tuple[str, str, str], str] = {
    ("hebbian_edges", "turn", "graph_store.save"):     "HebbianGraphStore.save — the post-turn persist",
    ("hebbian_edges", "cycle", "graph_store.save"):    "HebbianGraphStore.save — the cycle's persist",
    ("triples", "turn", "tiered_store.fire"):          "TripleStore.fire — reinforcement by citation",
    ("triples", "cycle", "tiered_store.rebalance"):    "TripleStore.rebalance — tier moves",
    ("triples", "cycle", "tiered_store.add"):          "Neocortex promotion entry — consolidation and procedural promotion",
    ("relation_trails", "cycle", "trails._lay_range"): "TrailStore.scan_new — trail rows (deferred under frozen)",
    ("trail_cursor", "cycle", "trails.scan_new"):      "TrailStore.scan_new — the cursor advance (deferred under frozen)",
}

_RECORD_TABLES = {"lineage_records", "lineage_injections", "scope_meta", "work_unit",
                  "triple_work_unit", "archive_blocks",
                  # W6 — the boundary ledger: a close or a reopen is the record's
                  # attribution layer, made after the turn's record.
                  "work_unit_events",
                  # W8 — a record's salted commitments, in its transaction.
                  "record_salts", "record_commitments",
                  # W0b — the replay capture's companions, written by
                  # `record_turn` in the record's own transaction.
                  "request_bodies", "inference_configs", "environments"}


class WriteTrace:
    """A context manager: every connection opened inside it is traced."""

    def __init__(self, extra_modules: tuple = ()) -> None:
        """`extra_modules`: dotted module-name prefixes whose frames count as
        backend frames — the companions plant fixture writers under
        `tests.fixtures.` and need the trace to see them as writers."""
        self.writes: collections.Counter = collections.Counter()   # (table, chain) -> n
        self.set_columns: dict[str, set[str]] = collections.defaultdict(set)   # per (table, method): SET columns
        self.extra_modules = tuple(extra_modules)

    def __enter__(self) -> "WriteTrace":
        # W3b — the trace rides the store interface's hook (`backend.store`):
        # every statement any `Database` runs is reported with its text, once
        # per parameter set of an executemany, on every backend. Before W3b
        # this monkeypatched `sqlite3.connect` and attached SQLite's
        # `set_trace_callback`, which no other backend has (agent B's G-8).
        from backend.store import add_trace
        add_trace(self._cb)
        return self

    def __exit__(self, *exc) -> None:
        from backend.store import remove_trace
        remove_trace(self._cb)

    def _stack(self) -> list:
        """The live frames that issued the statement, outermost first — real
        frame objects, so each carries its module's `__name__`."""
        frames = []
        f = sys._getframe(1)
        while f is not None:
            frames.append(f)
            f = f.f_back
        frames.reverse()
        return frames

    def _cb(self, sql: str) -> None:
        m = _WRITE.match(sql)
        if not m:
            return
        table = m.group(3)
        chain = chain_of(self._stack(), self.extra_modules)
        self.writes[(table, chain)] += 1
        if sql.lstrip().upper().startswith("UPDATE"):
            cols = _SET_COLS.search(sql)
            if cols:
                names = {c.split("=")[0].strip() for c in cols.group(1).split(",")}
                self.set_columns[(table, method_of(chain))] |= names

    def clear(self) -> None:
        self.writes.clear()
        self.set_columns.clear()

    def pairs(self) -> collections.Counter:
        out: collections.Counter = collections.Counter()
        for (table, chain), n in self.writes.items():
            out[(table, host_of(chain), method_of(chain))] += n
        return out

    def tables(self) -> collections.Counter:
        out: collections.Counter = collections.Counter()
        for (table, _chain), n in self.writes.items():
            out[table] += n
        return out


def _pure(filename: str) -> PurePath:
    """A path string parsed in the flavour it was written in, not the
    host's: `PurePath` on Linux does not split a backslash, which is the
    class of defect this module exists because of."""
    if "\\" in filename or re.match(r"^[A-Za-z]:", filename):
        return PureWindowsPath(filename)
    return PurePosixPath(filename)


def frame_tag(frame, extra_modules: tuple = ()) -> tuple | None:
    """("backend" | "scripts", module stem, function) for one frame, from
    the module's `__name__`; None for a frame that is neither. Accepts any
    object with `f_globals` and `f_code.co_name` — a live frame or a
    synthetic one."""
    g = frame.f_globals
    name = g.get("__name__") or ""
    func = frame.f_code.co_name
    if name == "backend.store" or name.startswith("backend.store."):
        # W3b — the store interface is the driver's seat: a frame inside it
        # reports the statement, it never issues one on its own account, so
        # it is no more a writer than the sqlite3 frames the pre-W3b trace
        # callback fired from. Without this the innermost backend frame of
        # every write would read `base._notify`.
        return None
    if name == "backend" or name.startswith("backend.") or any(name.startswith(m) for m in extra_modules):
        return ("backend", name.rsplit(".", 1)[-1], func)
    if name.startswith("scripts."):
        return ("scripts", name.rsplit(".", 1)[-1], func)
    if name == "__main__":
        # A script run as a program (`python scripts/x.py`, or runpy in
        # `drive_scripts`) has no dotted name; its own directory names it.
        # The one remaining path read, and it goes through pathlib.
        file = _pure(g.get("__file__") or "")
        if file.parent.name == "scripts":
            return ("scripts", file.stem, func)
    return None


def chain_of(frames, extra_modules: tuple = ()) -> tuple:
    """The normalised chain for a stack given outermost first: every frame
    `frame_tag` names, in order. Pure — the trace feeds it live frames and
    T11 feeds it synthetic ones."""
    return tuple(t for t in (frame_tag(f, extra_modules) for f in frames) if t is not None)


def host_of(chain: tuple) -> str:
    names = [name for _k, _stem, name in chain]
    if "_run_cycle_body" in names:
        return "cycle"
    if "run_turn" in names or any(stem == "server" and name == "gen" for _k, stem, name in chain):
        return "turn"
    if any(name in ("create_services", "create_app") for name in names):
        return "boot"
    if chain and chain[0][0] == "scripts":
        return f"script:{chain[0][1]}"
    if chain and chain[0][1] == "server":
        return f"route:{chain[0][2]}"
    if any(stem in ("lineage", "recalibration", "trails", "graph_store", "tiered_store", "work_units")
           and name == "__init__" for _k, stem, name in chain):
        return "boot"
    return f"other:{chain[0][1]}.{chain[0][2]}" if chain else "other:?"


def method_of(chain: tuple) -> str:
    backend = [(stem, name) for k, stem, name in chain if k == "backend"]
    if not backend:
        return "?"
    stem, name = backend[-1]
    return f"{stem}.{name}"


def classify(pair: tuple[str, str, str]) -> str | None:
    """record | claim path | confirmed act | learning | None (unanswered)."""
    table, host, method = pair
    if pair in LEARNING_PAIRS:
        return "learning"
    if table in _RECORD_TABLES:
        return "record"
    if method.endswith(".mark_sealed") or (".mark_" in method and method.endswith("_sealed")):
        return "record"                      # the seal backfill on any governance table, and W9's held commitments
    if table == "trail_cursor" and method == "trails._ensure_cursor":
        return "record"                      # the cursor's one-time initialisation, bookkeeping
    if pair == ("triples", "turn", "tiered_store.add"):
        return "claim path"
    if host.startswith("route:"):
        return "confirmed act"
    return None


# ---------------------------------------------------------------------------
# drivers, derived
# ---------------------------------------------------------------------------

FRAMEWORK_PATHS = {"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"}


def write_routes(app) -> list[tuple[str, str]]:
    """(method, path) for every non-GET route on the app's own router."""
    out = []
    for route in app.routes:
        for m in sorted(getattr(route, "methods", None) or ()):
            if m in ("GET", "HEAD", "OPTIONS") or route.path in FRAMEWORK_PATHS:
                continue
            out.append((m, route.path))
    return sorted(set(out))


# Routes that need the network cannot be driven with a fixture; named, with
# the reason, pinned by the test that prints them.
ROUTE_EXEMPTIONS: dict[tuple[str, str], str] = {
    ("POST", "/kb/seed/conceptnet"): "needs the network (ConceptNet)",
    ("POST", "/kb/seed/wikidata"):   "needs the network (Wikidata SPARQL)",
    ("POST", "/search/execute"):     "needs the network (SearXNG)",
}


def fixture_payloads(fx) -> dict[tuple[str, str], dict]:
    """A confirmed payload per write route, over the recall fixture (rev3,
    rev4 and a third claim; twelve sealed records). `query` carries query
    parameters; `json` the body."""
    return {
        ("POST", "/chat"):                       {"json": {"message": "what does the isolation step require?"}},
        ("POST", "/kairos/cycle"):               {"query": "force_snapshot=false"},
        ("POST", "/kb/assert/confirm"):          {"json": {"sentence": "The dose was 600mg.", "confirmed": True}},
        ("POST", "/commit"):                     {"json": {"content": "a note to hold", "confirmed": True}},
        ("POST", "/kb/index/confirm"):           {"json": {"confirmed": True, "limit": 5}},
        ("POST", "/kb/trails/backfill/confirm"): {"query": "confirmed=true"},
        ("POST", "/outcome/confirm"):            {"json": {"event_type": "review", "decision": "approved",
                                                           "direction": "confirming", "magnitude": 0.05,
                                                           "references": {"belief_ids": [fx.rev3]},
                                                           "confirmed": True}},
        ("POST", "/governance/envelope/confirm"): {"json": {"version": "fz-fixture", "max_delta": 0.1,
                                                            "movable_classes": ["asserted"],
                                                            "reason": "FZ-2 fixture", "confirmed": True}},
        ("POST", "/kb/dedup/confirm"):           {"json": {"subject_a": "sample isolation", "relation_a": "requires",
                                                           "object_a": "centrifuge at 3000g for 10 minutes",
                                                           "subject_b": "sample isolation", "relation_b": "requires",
                                                           "object_b": "centrifuge at 3000g for 12 minutes",
                                                           "keep": "a", "confirmed": True}},
        ("POST", "/kb/seed/offline"):            {"query": "confirmed=true"},
        ("POST", "/model/override"):             {"json": {"model": "code", "lock": False}},
        ("DELETE", "/model/override"):           {},
        ("POST", "/search/confirm"):             {"json": {"query": "q", "confirmed": True}},
    }


def drive_routes(client, fx, headers: dict) -> dict:
    """Drive every derived write route once. Returns what was driven, what
    was exempt, and what had no fixture (an undriven route is a finding)."""
    payloads = fixture_payloads(fx)
    driven, exempt, undriven = [], [], []
    for method, path in write_routes(client.app):
        key = (method, path)
        if key in ROUTE_EXEMPTIONS:
            exempt.append((key, ROUTE_EXEMPTIONS[key]))
            continue
        spec = payloads.get(key)
        if spec is None:
            undriven.append(key)
            continue
        url = path + (("?" + spec["query"]) if spec.get("query") else "")
        r = client.request(method, url, json=spec.get("json"), headers=headers)
        driven.append((key, r.status_code))
    return {"driven": driven, "exempt": exempt, "undriven": undriven}


_STORE_MODULES = ("backend.kb.tiered_store", "backend.memory.lineage", "backend.kb.recalibration",
                  "backend.memory.graph_store", "backend.kb.trails", "backend.memory.chain",
                  "backend.memory.neocortex", "backend.kb.semantic_index", "backend.kb.work_units",
                  "backend.server", "backend.store")                      # W3b: the interface package


def _imports(tree: ast.Module) -> set[str]:
    mods = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module:
            mods.add(n.module)
    return mods


def _options(tree: ast.Module) -> set[str]:
    return {a.value for n in ast.walk(tree) if isinstance(n, ast.Call)
            and getattr(n.func, "attr", "") == "add_argument"
            for a in n.args if isinstance(a, ast.Constant) and isinstance(a.value, str) and a.value.startswith("--")}


def store_scripts() -> list[dict]:
    """Every tracked script that imports a store module, by AST, with its
    dry mode (an opt-in `--confirm`/`--write` whose absence is dry) and its
    store-path option, when it has them."""
    out = []
    for p in tracked("scripts/*.py"):
        p = Path(p)
        if p.suffix != ".py":
            continue
        tree = ast.parse(p.read_text(encoding="utf-8"))
        if not any(m in _STORE_MODULES for m in _imports(tree)):
            continue
        opts = _options(tree)
        dry = sorted(o for o in opts if o in ("--confirm", "--write") or "dry" in o)
        path_opt = next((o for o in ("--kb", "--db", "--store") if o in opts), None)
        out.append({"name": p.stem, "path": p, "dry_mode": dry, "path_option": path_opt,
                    "options": sorted(opts)})
    return out


def drive_scripts(kb_path: Path, extra_env: dict | None = None) -> dict:
    """Run every store-importing script that HAS a dry mode and a store-path
    option, in that mode, against `kb_path`, in this process (so the trace
    sees it). The rest are named exemptions with the reason."""
    driven, exempt = [], []
    for s in store_scripts():
        if not s["dry_mode"]:
            exempt.append((s["name"], "no dry mode (a probe or harness: reads, or writes only its own scratch store)"))
            continue
        if not s["path_option"]:
            exempt.append((s["name"], "dry mode but no store-path option to point at the rig"))
            continue
        argv = [str(s["path"]), s["path_option"], str(kb_path)]
        # A dry run must not leave a file in the repository: the scripts
        # write their reports relative to the working directory (and to
        # `--report` where they take one), so both point at a scratch dir.
        scratch = Path(tempfile.mkdtemp(prefix="fz_script_"))
        if "--report" in s["options"]:
            argv += ["--report", str(scratch / f"{s['name']}.json")]
        old_argv, old_env, old_cwd = sys.argv, dict(os.environ), os.getcwd()
        try:
            sys.argv = argv
            os.environ.update(extra_env or {})
            os.chdir(scratch)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                try:
                    runpy.run_path(str(s["path"]), run_name="__main__")
                    rc = 0
                except SystemExit as e:
                    rc = int(e.code or 0) if isinstance(e.code, int) or e.code is None else 1
            driven.append((s["name"], rc))
        except Exception as e:                     # a script that cannot run here is declared, not hidden
            driven.append((s["name"], f"error:{type(e).__name__}"))
        finally:
            sys.argv = old_argv
            os.chdir(old_cwd)
            os.environ.clear(); os.environ.update(old_env)
    return {"driven": driven, "exempt": exempt}


def plant_counters(svc, pairs=None) -> int:
    """Edges at fire_count 9 / recurrence 4, the shape the operator's store
    has; the fresh-store gate admits nothing in 60 turns (FZ S1.2)."""
    pairs = pairs or [("interval training", "vo2 max"), ("interval training", "resting heart rate"),
                      ("caffeine", "endurance"), ("sleep", "recovery"), ("creatine", "strength")]
    for s, o in pairs:
        svc.hippocampus.ingest_triple(s, "co_activated", o, weight=0.6)
        e = svc.hippocampus.get_edge(s, o)
        e.fire_count, e.recurrence_count = 9, 4
    return len(pairs)


def drive_turns_and_cycles(client, headers: dict, *, turns: int = 60, every: int = 10,
                           questions=None) -> list:
    questions = questions or ["what does interval training improve?", "and what does it reduce?",
                              "so is it good for recovery?", "does caffeine improve endurance?"]
    promoted = []
    for t in range(turns):
        r = client.post("/chat", json={"message": questions[t % len(questions)]}, headers=headers)
        assert r.status_code == 200, r.text
        if every and (t + 1) % every == 0:
            res = client.post("/kairos/cycle?force_snapshot=false", headers=headers).json()
            promoted.append(res.get("edges_promoted"))
    return promoted


def drive_boundary_turns(client, headers: dict) -> list[dict]:
    """W6 — a unit named, closed and reopened on turns: the boundary act and
    its ledger row are written on a turn, after its record, and the guards
    that read the database see them only if a turn makes them."""
    out = []
    for boundary in (None, "close", "reopen"):
        body = {"message": "what does interval training improve?", "work_unit": "fz-unit",
                "accountable": "fz-tester", "actor_id": "fz-tester"}
        if boundary:
            body["decision_boundary"] = boundary
        r = client.post("/chat", json=body, headers=headers)
        assert r.status_code == 200, r.text
        out.append(done_frames(r.text)[-1])
    return out


def done_frames(resp_text: str) -> list[dict]:
    return [json.loads(f.split("data: ", 1)[1]) for f in resp_text.split("\n\n") if f.startswith("event: done")]


__all__ = ["WriteTrace", "LEARNING_PAIRS", "ROUTE_EXEMPTIONS", "classify", "host_of", "method_of",
           "frame_tag", "chain_of",
           "write_routes", "fixture_payloads", "drive_routes", "store_scripts", "drive_scripts",
           "plant_counters", "drive_turns_and_cycles", "drive_boundary_turns", "done_frames", "REPO"]
