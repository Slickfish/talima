"""Packet J.1 — importing `backend.server` must have no side effects.

WHY THIS IS A SENTINEL AND NOT A DOCSTRING
    `scripts/verify_packet_f.py` carries a docstring saying it deliberately
    does not import `backend.server`, **for exactly this reason**, and the
    rule was then broken by its own author in the same session — a scratchpad
    probe imported the module and migrated the operator's live `data/kb.db`
    (Packet H.2: `pre-import md5 3697657…` -> `post-import md5 32b12d8…`,
    `has archived: False -> True`).

    Pack §4 states the lesson: *a documented rule is not a control.* Every
    other invariant in this project has been converted from prose into a
    sentinel. This is the one that had not been, and it is the one that had
    already caused a real write to the operator's store.

WHY A SUBPROCESS, AND NOT AN IMPORT IN THIS PROCESS
    By the time any test runs, `conftest.py` has already imported
    `backend.server` — deliberately, and after patching the eight store
    constructors so the import lands on a temp tree. Re-importing here would
    hit `sys.modules` and measure nothing. Each guard therefore spawns a
    **fresh interpreter** with a temp working directory, because the path
    defaults are relative (`Path("data/kb.db")`, never `.resolve()`d) and so
    resolve against the cwd at open time. The cwd is the whole mechanism.

WHAT EACH GUARD IS FOR
    writes_no_file    Was xfail(strict) in J.1, measuring 3 files and 2
                      directories created by the import. J.2 removed the
                      module-scope `app = create_app()` and it now passes:
                      the footprint is empty.
    opens_no_socket   UNMARKED throughout — it was already true, and is
                      written to stay true. The Ollama probe lives in the
                      FastAPI lifespan, a different thing on a different
                      trigger (J.0.8). Marking this xfail(strict) would XPASS
                      and fail the suite, which is how it was nearly shipped.

                      **NARROWED BY M.3, AND THE WORDING MATTERS.** It used to
                      say "import opens nothing", which is false on Windows
                      and was never true anywhere — it only looked true in a
                      sandbox with no usable IPv6. The claim is now:

                        NEXUS's import performs NO OUTBOUND NETWORK ACCESS. A
                        transitive dependency (urllib3, via langgraph ->
                        langchain_core -> requests) performs a local IPv6
                        CAPABILITY PROBE: loopback bind, ephemeral port, no
                        listen, no connect.

                      `bind` and `connect` are different claims and the guard
                      now distinguishes them. Every bind is still DECLARED on
                      every run (I-6) — see `_declare`.
    factory_invocable IN-PROCESS ONLY. It proves the factory yields a working
                      ASGI app. It does NOT prove `python start.py` launches
                      on Windows — that is the operator's acceptance test and
                      is stated as such rather than implied.
"""
import hashlib
import json
from tests import _proc
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# What a bare import used to create, before J.2 removed the module-scope
# `app = create_app()`. Kept as a comment rather than a constant because
# nothing asserts it any more — it is the historical measurement, and the
# breakdown is the useful part: eight persistent writers collapse to three
# files because four of them share kb.db.
#
#   data/kb.db                   TripleStore + Neocortex + LineageStore
#                                  + HebbianGraphStore  (4 writers, 1 file)
#   data/archive.db              NexusArchiveChain
#   data/chroma/                 VectorStore (directory)
#   data/chroma/chroma.sqlite3   VectorStore
#   data/snapshots/              KairosProcess (directory)


# ---------------------------------------------------------------------------
# The probe: a fresh interpreter, a temp cwd, and a fingerprint either side
# ---------------------------------------------------------------------------

_PROBE = textwrap.dedent('''
    import hashlib, json, pathlib, socket, sys
    sys.path.insert(0, {repo!r})

    def snap(root):
        """(size, mtime_ns, md5) per file; presence per directory.

        Packet A's pattern, and its refinement: `st_mtime_ns` is included
        because a (name, size) fingerprint missed a real mutation once — the
        chroma leak changed only the mtime.

        KEY FORMAT IS POSIX, ALWAYS (Packet M.1). `str(Path.relative_to(...))`
        renders `data\\side_effect.txt` on Windows, so the guard reported a
        created file and the assertion looking for `data/side_effect.txt`
        could not recognise the guard's own output. Normalising HERE rather
        than at each assertion is deliberate: every consumer of this dict gets
        the same contract, and a future assertion cannot reintroduce the bug
        by writing a `/` literal — which is what it should be able to write.
        """
        out = {{}}
        for p in sorted(pathlib.Path(root).rglob("*")):
            key = p.relative_to(root).as_posix()
            if p.is_dir():
                out[key] = "<dir>"
            else:
                st = p.stat()
                out[key] = (st.st_size, st.st_mtime_ns,
                            hashlib.md5(p.read_bytes()).hexdigest())
        return out

    import traceback

    # Packet M.2 — the events now carry a STACK. The Windows run reported
    # `[['bind', "('::1', 0)"]]` and there was no way to tell what called it:
    # an event without a caller names a symptom. `detail` holds the frames
    # below this spy, innermost last, so the failure message identifies the
    # mechanism rather than inviting a guess about it.
    #
    # `listen` is instrumented as of M.2 and is the DISCRIMINATOR the question
    # needs: bind-then-listen is a server socket (a real finding); bind with
    # no listen is a probe or an ephemeral-port grab (interpreter machinery).
    # `accept` likewise. Neither was watched before, so the earlier evidence
    # could not distinguish the two.
    events = []

    def _frames():
        out = []
        for fr in traceback.extract_stack()[:-2]:      # drop spy + _frames
            out.append(f"{{fr.filename}}:{{fr.lineno}} in {{fr.name}}"
                       + (f"  |  {{fr.line}}" if fr.line else ""))
        return out[-14:]

    for _name in ("connect", "connect_ex", "bind", "sendto", "listen",
                  "accept"):
        _real = getattr(socket.socket, _name)
        def _mk(n, r):
            def spy(self, *a, **kw):
                try:
                    fam = getattr(self, "family", None)
                    fam = getattr(fam, "name", str(fam))
                    typ = getattr(self, "type", None)
                    typ = getattr(typ, "name", str(typ))
                except Exception:
                    fam = typ = "?"
                events.append([n, str(a[0]) if a else "",
                               {{"family": fam, "type": typ,
                                 "stack": _frames()}}])
                return r(self, *a, **kw)
            return spy
        setattr(socket.socket, _name, _mk(_name, _real))
    _rcc = socket.create_connection
    socket.create_connection = lambda addr, *a, **kw: (
        events.append(["create_connection", str(addr),
                       {{"stack": _frames()}}]) or _rcc(addr, *a, **kw))
    _rgai = socket.getaddrinfo
    socket.getaddrinfo = lambda h, p, *a, **kw: (
        events.append(["getaddrinfo", f"{{h}}:{{p}}",
                       {{"stack": _frames()}}]) or _rgai(h, p, *a, **kw))

    CWD = pathlib.Path(".").resolve()
    before = snap(CWD)
    {import_stmt}
    after = snap(CWD)

    print("@@RESULT@@" + json.dumps({{
        "created": sorted(set(after) - set(before)),
        "changed": sorted(k for k in set(after) & set(before)
                          if after[k] != before[k]),
        "sockets": events,
        "n_before": len(before),
    }}))
''')


def _fmt_events(events) -> str:
    """Render socket events with their captured stacks.

    M.2 — the Windows failure read `[['bind', "('::1', 0)"]]` and nothing
    more. A reader could not tell whether that was NEXUS opening a listener or
    the interpreter building a self-pipe, and those are opposite conclusions.
    The formatter exists so the failure message answers that instead of
    posing it.
    """
    if not events:
        return "(none)"
    out = []
    for ev in events:
        name, addr = ev[0], ev[1]
        detail = ev[2] if len(ev) > 2 and isinstance(ev[2], dict) else {}
        head = f"  {name}({addr})"
        if detail.get("family"):
            head += f"   [{detail['family']} / {detail.get('type')}]"
        out.append(head)
        for frame in detail.get("stack", []):
            out.append(f"        {frame}")
    return "\n".join(out)


# Events that mean "this process reached the network", as distinct from
# "this process asked the OS for a socket". This is the property Packet J
# actually asserts, and as of M.3 it is what the guards assert too.
_OUTBOUND = ("connect", "connect_ex", "sendto", "create_connection",
             "getaddrinfo")


def _outbound(events) -> list:
    return [ev for ev in events if ev[0] in _OUTBOUND]


# ---------------------------------------------------------------------------
# M.3 — the ONE excluded event, and the evidence for excluding it
# ---------------------------------------------------------------------------
# The Windows run reported `[['bind', "('::1', 0)"]]` at import and again at
# construction, and both socket guards failed. M.2 identified the caller by
# instrumenting `socket.__init__` during `import backend.server` and reading
# the frames. VERBATIM:
#
#   <frozen importlib._bootstrap_external>:940 in exec_module
#   <frozen importlib._bootstrap>:241 in _call_with_frames_removed
#   .../urllib3/util/connection.py:137 in <module>  |  HAS_IPV6 = _has_ipv6("::1")
#   .../urllib3/util/connection.py:126 in _has_ipv6 |  sock = socket.socket(socket.AF_INET6)
#
# and the function itself (urllib3/util/connection.py:114-133):
#
#       def _has_ipv6(host):
#           """Returns True if the system can bind an IPv6 address."""
#           if socket.has_ipv6:
#               try:
#                   sock = socket.socket(socket.AF_INET6)
#                   sock.bind((host, 0))          # <- the observed event
#                   has_ipv6 = True
#               except Exception:
#                   pass
#           if sock: sock.close()
#           return has_ipv6
#
# Reached via backend.server -> backend.memory.checkpoint -> langgraph ->
# langchain_core -> requests -> urllib3. It is a CAPABILITY PROBE: ask the OS
# for an ephemeral loopback port, learn whether IPv6 works, close. It binds
# and never listens, never accepts, never connects, and never resolves a name.
#
# Why Linux never saw it: this sandbox has no usable IPv6, so
# `socket.socket(AF_INET6)` raises OSError Errno 97 at CONSTRUCTION — before
# the bind — and urllib3's bare `except Exception` swallows it. Same code,
# both platforms; only the OS differs.
#
# WHAT IS EXCLUDED, AND WHAT DELIBERATELY IS NOT.
# The exclusion is as narrow as the evidence: AF_INET6, a loopback address,
# port 0, and no `listen` on the same socket. A bind that is followed by
# `listen` STILL TRIPS — that is the whole M.2.3 question, "does create_app()
# open a listener", and narrowing it away would delete the only guard that
# can answer it. `listen`/`accept` were instrumented in M.2 precisely so this
# distinction could be made rather than assumed.
#
# This is NOT "ignore binds". A bind to a routable address, to a fixed port,
# over AF_INET, or followed by a listen, all still fail the guard.

_LOOPBACK_V6 = ("::1", "0:0:0:0:0:0:0:1")


def _is_ipv6_capability_probe(event, events) -> bool:
    """True for the urllib3 `_has_ipv6` bind, and for nothing wider."""
    name, addr = event[0], event[1]
    detail = event[2] if len(event) > 2 and isinstance(event[2], dict) else {}
    if name != "bind":
        return False
    if detail.get("family") != "AF_INET6":
        return False
    # The address arrives as the repr of the tuple passed to bind().
    if not any(f"'{h}'" in addr for h in _LOOPBACK_V6):
        return False
    if not (addr.endswith(", 0)") or addr.endswith(",0)")):
        return False                     # ephemeral port only
    # A socket that goes on to listen is a SERVER, not a probe. The spy does
    # not carry socket identity, so this is deliberately conservative: any
    # listen anywhere in the run disqualifies the exclusion and the guard
    # fails, which is the safe direction to be wrong in.
    return not any(ev[0] in ("listen", "accept") for ev in events)


def _unexplained(events) -> list:
    """Socket events this guard does not have evidence to excuse."""
    return [ev for ev in events if not _is_ipv6_capability_probe(ev, events)]


def _declare(events, label: str) -> None:
    """I-6 — declare every socket event, including the excused one.

    Packet L.0.10 made the same argument about `/kb/stats`: a surface that
    reports only failures lets a second, benign-looking event be absorbed
    into an exclusion written for the first. So every bind is printed on
    every run, passing or not. pytest hides this unless `-s` or the test
    fails, which is the right default — it is a record, not a warning.
    """
    if not events:
        return
    print(f"\n[{label}] socket events observed ({len(events)}), "
          f"{len(_unexplained(events))} unexplained, "
          f"{len(_outbound(events))} outbound:\n{_fmt_events(events)}")


def _probe(tmp_path: Path, import_stmt: str, *, seed_data=True) -> dict:
    """Run `import_stmt` in a fresh interpreter rooted at `tmp_path`."""
    work = tmp_path / "work"
    (work / "data").mkdir(parents=True, exist_ok=True)
    if seed_data:
        # NON-VACUITY: a purity guard over an empty tree proves nothing about
        # the `changed` set, because there is nothing that could change. So
        # the tree is seeded — but deliberately NOT with `kb.db`/`archive.db`.
        #
        # The first draft seeded those two with placeholder bytes and the
        # probe died on `sqlite3.DatabaseError: file is not a database`: the
        # import opens them, so seeding them tests SQLite's reaction to
        # garbage rather than the purity of the import. The files under
        # measurement must be absent; the fixture proves the `changed`
        # detector works using files nothing under test touches.
        (work / "data" / "notes.txt").write_text("pre-existing", encoding="utf-8")
        (work / "data" / "prior.json").write_text('{"kept": true}', encoding="utf-8")
        (work / "data" / "nested").mkdir(exist_ok=True)
        (work / "data" / "nested" / "deep.txt").write_text("deep", encoding="utf-8")

    script = _PROBE.format(repo=str(ROOT), import_stmt=import_stmt)
    proc = _proc.run([sys.executable, "-c", script], cwd=work,
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          timeout=180)
    assert proc.returncode == 0, (
        f"probe interpreter failed:\nSTDOUT{proc.stdout}\nSTDERR{proc.stderr}")
    line = next((l for l in proc.stdout.splitlines()
                 if l.startswith("@@RESULT@@")), None)
    assert line, f"probe produced no result:\n{proc.stdout}\n{proc.stderr}"
    return json.loads(line[len("@@RESULT@@"):])


# ---------------------------------------------------------------------------
# 1. Files
# ---------------------------------------------------------------------------

# FLIPPED BY J.2 — was xfail(strict): the import created 3 files and 2
# directories (data/kb.db, data/archive.db, data/chroma/,
# data/chroma/chroma.sqlite3, data/snapshots/) because server.py ran
# `app = create_app()` at module scope. That line is gone; the footprint is
# now empty. Its paired pin, test_the_import_artefacts_are_exactly_what_we_
# think, was deleted in the same commit — it existed to hold the old
# footprint on record and asserting an empty set would be a different test
# wearing its name.
def test_importing_server_writes_no_file(tmp_path):
    """A bare import must not touch the filesystem.

    This is the guard that would have stopped the H.2 incident: a scratchpad
    script imported the module from the repo root and migrated the operator's
    live database. Nothing about that script was careless except the import.
    """
    result = _probe(tmp_path, "import backend.server")

    # Non-vacuity: the fixture tree must be non-empty, or "nothing changed" is
    # a statement about an empty directory.
    assert result["n_before"] >= 4, (
        f"fixture tree is too small to prove anything: {result['n_before']} entries")

    assert not result["created"], (
        f"import created {len(result['created'])} path(s): {result['created']}")
    assert not result["changed"], (
        f"import modified {len(result['changed'])} existing path(s): "
        f"{result['changed']}")


# ---------------------------------------------------------------------------
# 2. Sockets — UNMARKED, already true
# ---------------------------------------------------------------------------

def test_importing_server_opens_no_socket(tmp_path):
    """Import opens no network connection — and must keep opening none.

    Deliberately NOT xfail: measured, this already holds. `connect`,
    `connect_ex`, `bind`, `sendto`, `create_connection` and `getaddrinfo` are
    all instrumented and all silent during import.

    The Ollama probe that costs the suite 90 round trips lives in the FastAPI
    **lifespan**, not in construction, and fires per `TestClient` entry
    (J.0.8, three-stage measurement: import [], construction [], lifespan
    [('127.0.0.1', 11434)]). Those are different triggers, and this guard is
    here so that a future change cannot quietly move one into the other.
    """
    result = _probe(tmp_path, "import backend.server")
    _declare(result["sockets"], "import backend.server")

    assert _outbound(result["sockets"]) == [], (
        "IMPORT REACHED THE NETWORK. This is the property Packet J exists to "
        "protect and it has been violated:\n"
        + _fmt_events(_outbound(result["sockets"])))

    assert _unexplained(result["sockets"]) == [], (
        "import performed a socket operation this guard has no evidence to "
        "excuse. The only excused event is urllib3's IPv6 capability probe "
        "(AF_INET6 loopback bind, ephemeral port, no listen) — see the M.3 "
        "note above for the trace. Anything else is unexplained, and a bind "
        "followed by a listen is a SERVER, not a probe:\n"
        + _fmt_events(_unexplained(result["sockets"])))


def test_construction_opens_no_socket_either(tmp_path):
    """The middle stage of the three-stage measurement, pinned.

    Building the app is silent; only entering the lifespan probes Ollama.
    Recorded here because J.2 will be asked whether the factory change makes
    the 90 probes removable, and this is the evidence that the answer is no:
    the probe is not on the construction path at all.
    """
    result = _probe(tmp_path, textwrap.dedent("""
        import tempfile, pathlib
        from backend.server import create_app, create_services
        d = pathlib.Path(tempfile.mkdtemp())
        app = create_app(create_services(
            kb_db_path=d/"kb.db", chroma_dir=d/"chroma",
            eval_log_path=d/"m.jsonl", archive_db_path=d/"a.db",
            snapshots_dir=d/"snap"))
    """).strip())
    _declare(result["sockets"], "create_app() construction")

    assert _outbound(result["sockets"]) == [], (
        "app construction reached the network:\n"
        + _fmt_events(_outbound(result["sockets"])))

    assert _unexplained(result["sockets"]) == [], (
        "app construction performed an unexcused socket operation. A `bind` "
        "FOLLOWED BY `listen` here would mean create_app() opens a listener "
        "at construction time — a real finding, not a test defect, and the "
        "reason `listen` is instrumented at all:\n"
        + _fmt_events(_unexplained(result["sockets"])))


# ---------------------------------------------------------------------------
# 3. Non-vacuity — the guards must be able to FAIL
# ---------------------------------------------------------------------------

def test_a_deliberately_impure_import_trips_the_file_guard(tmp_path):
    """A purity test that cannot detect impurity is the defect class this
    project keeps finding (pack §15.4). Prove it detects.

    Uses a module whose import writes a file and modifies an existing one —
    the same two things the real import does.
    """
    impure = tmp_path / "impure_pkg"
    impure.mkdir()
    (impure / "impure_mod.py").write_text(textwrap.dedent("""
        import pathlib
        pathlib.Path("data/side_effect.txt").write_text("written at import",
                                                        encoding="utf-8")
        pathlib.Path("data/notes.txt").write_text("mutated at import",
                                                   encoding="utf-8")
    """), encoding="utf-8")

    result = _probe(tmp_path, f"import sys; sys.path.insert(0, {str(impure)!r}); "
                              f"import impure_mod")
    # Posix keys by contract — see `snap()`. On Windows this assertion read
    # `'data/side_effect.txt' in ['data\\side_effect.txt']` and failed while
    # the guard underneath it was working perfectly (Packet M.1).
    assert "data/side_effect.txt" in result["created"], (
        f"the file guard did not notice a file created during import: "
        f"{result['created']}")
    assert "data/notes.txt" in result["changed"], (
        f"the file guard did not notice an existing file modified during "
        f"import: {result['changed']}")


def test_probe_keys_are_posix_on_every_platform(tmp_path):
    """The contract `snap()` promises, asserted rather than assumed.

    On Linux this can only ever pass — `str()` and `as_posix()` agree — so it
    is documentation here and a real guard on Windows. It exists because the
    M.1 failure was not a broken guard but a broken *contract between* the
    guard and its assertions, and that class of defect is invisible until the
    platform that separates the two cases runs it.

    Packet I fixed the same class one file over (test_encoding_parity.py:276
    carries `.replace("\\\\", "/")`) and did not sweep for siblings. This is
    the sibling.
    """
    result = _probe(tmp_path, "pass")
    keys = result["created"] + result["changed"] + [
        k for k in (result.get("keys") or [])]
    seeded = _probe(tmp_path / "b", "import pathlib; "
                                    "pathlib.Path('data/x/y.txt').parent.mkdir("
                                    "parents=True, exist_ok=True); "
                                    "pathlib.Path('data/x/y.txt').write_text("
                                    "'hi', encoding='utf-8')")
    keys += seeded["created"]
    assert any("/" in k for k in keys), (
        f"non-vacuity: no nested key was produced, so separator handling was "
        f"never exercised: {keys}")
    assert not [k for k in keys if "\\" in k], (
        f"probe emitted a non-posix key — assertions written with '/' cannot "
        f"match it: {[k for k in keys if chr(92) in k]}")


def test_a_deliberately_impure_import_trips_the_socket_guard(tmp_path):
    """Same, for the network guard — which is unmarked and therefore the one
    most at risk of passing forever without ever having been able to fail."""
    impure = tmp_path / "impure_net"
    impure.mkdir()
    (impure / "impure_net_mod.py").write_text(textwrap.dedent("""
        import socket
        s = socket.socket()
        s.settimeout(0.05)
        try:
            s.connect(("127.0.0.1", 9))     # discard port; refused instantly
        except OSError:
            pass
        finally:
            s.close()
    """), encoding="utf-8")

    result = _probe(tmp_path, f"import sys; sys.path.insert(0, {str(impure)!r}); "
                              f"import impure_net_mod")
    assert result["sockets"], (
        "the socket guard did not notice a connection opened during import")
    # Match on the ADDRESS field only. M.2 attached a stack to every event,
    # and `str(ev)` over the whole record now contains file paths and line
    # numbers — `"9" in str(ev)` would pass on a line number and the
    # non-vacuity check would be silently vacuous.
    assert any(ev[0] == "connect" and "9" in ev[1]
               for ev in result["sockets"]), _fmt_events(result["sockets"])
    assert _outbound(result["sockets"]), (
        "the outbound classifier did not recognise a real connect:\n"
        + _fmt_events(result["sockets"]))


# ---------------------------------------------------------------------------
# 4. The launch path — in-process only, and honest about it
# ---------------------------------------------------------------------------

def _ev(name, addr, family="AF_INET6"):
    return [name, addr, {"family": family, "type": "SOCK_STREAM", "stack": []}]


# The boundaries of M.3's exclusion, one case per way of being wrong. These
# are synthetic event records rather than real sockets because this sandbox
# has no usable IPv6 (`socket.socket(AF_INET6)` -> OSError Errno 97), so the
# excused case CANNOT be produced here at all — which is exactly why the
# exclusion has to be tested against its own predicate rather than inferred
# from a green run.
@pytest.mark.parametrize("events,excused,why", [
    ([_ev("bind", "('::1', 0)")], True,
     "the measured urllib3 probe"),
    ([_ev("bind", "('0:0:0:0:0:0:0:1', 0)")], True,
     "the same address, written long"),
    ([_ev("bind", "('::1', 0)"), _ev("listen", "")], False,
     "a bind followed by a LISTEN is a server, not a probe"),
    ([_ev("bind", "('::1', 0)"), _ev("accept", "")], False,
     "likewise accept"),
    ([_ev("bind", "('::1', 8080)")], False,
     "a fixed port is a service, not an ephemeral probe"),
    ([_ev("bind", "('fe80::1', 0)")], False,
     "not loopback"),
    ([_ev("bind", "('2001:db8::1', 0)")], False,
     "a routable address"),
    ([_ev("bind", "('127.0.0.1', 0)", family="AF_INET")], False,
     "AF_INET is a different probe with different evidence"),
    ([_ev("connect", "('::1', 0)")], False,
     "a connect is never excused, whatever it connects to"),
    ([_ev("sendto", "('::1', 0)")], False,
     "nor a sendto"),
])
def test_the_exclusion_accepts_only_the_measured_probe(events, excused, why):
    """A guard narrowed until it passes is worse than none, because it is
    trusted. Every way of being different from the measured probe must still
    be unexplained."""
    assert (_unexplained(events) == []) is excused, (
        f"{why}: expected excused={excused} for {events[0][:2]}")


def test_a_real_listener_still_trips_the_guard(tmp_path):
    """END-TO-END non-vacuity, with a real socket rather than a fixture.

    AF_INET, because that is what this sandbox can actually open — and it
    doubles as proof that the exclusion is family-specific. A module that
    binds a loopback ephemeral port AND LISTENS is a server, and M.2.3's
    question ("does create_app() open a listener?") depends on this tripping.
    """
    impure = tmp_path / "impure_listen"
    impure.mkdir()
    (impure / "impure_listen_mod.py").write_text(textwrap.dedent("""
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        s.close()
    """), encoding="utf-8")

    result = _probe(tmp_path, f"import sys; sys.path.insert(0, {str(impure)!r}); "
                              f"import impure_listen_mod")

    assert any(ev[0] == "listen" for ev in result["sockets"]), (
        f"the listen was not even instrumented: {_fmt_events(result['sockets'])}")
    assert _unexplained(result["sockets"]), (
        "a module that opened a LISTENER was excused by the M.3 exclusion — "
        f"the narrowing has swallowed the thing it was told to preserve:\n"
        + _fmt_events(result["sockets"]))
    # ...and it is correctly NOT counted as outbound: it never left the box.
    assert _outbound(result["sockets"]) == []


def test_the_declaration_reports_a_bind_even_when_the_guard_passes(capsys,
                                                                  tmp_path):
    """I-6 — declared, not merely tolerated.

    An exclusion that produces silence is how a SECOND benign-looking bind
    gets absorbed into a rule written for the first. `_declare` prints every
    socket event on every run, passing or failing, so the record exists
    before anyone needs it.
    """
    events = [_ev("bind", "('::1', 0)")]
    assert _unexplained(events) == [], "non-vacuity: this case must be excused"

    _declare(events, "synthetic")
    printed = capsys.readouterr().out
    assert "socket events observed (1)" in printed
    assert "0 unexplained" in printed
    assert "bind(('::1', 0))" in printed, (
        "an excused event was not printed — silence is how the next one hides")


def test_factory_is_invocable_and_yields_a_working_app():
    """The factory produces a real, serving ASGI app.

    SCOPE, STATED PLAINLY: this proves the factory works **in process**. It
    does NOT prove that `python start.py` or
    `uvicorn backend.server:create_app --factory` launches on the operator's
    Windows machine. That is the operator's acceptance test and cannot be
    performed here; claiming otherwise would be the kind of coverage-by-
    implication this project has spent several packets removing.
    """
    import tempfile
    from fastapi.testclient import TestClient
    from backend.server import create_app, create_services

    d = Path(tempfile.mkdtemp())
    app = create_app(create_services(
        kb_db_path=d / "kb.db", chroma_dir=d / "chroma",
        eval_log_path=d / "m.jsonl", archive_db_path=d / "a.db",
        snapshots_dir=d / "snap"))

    # Callable with no arguments too — the form `--factory` requires.
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
    assert app.routes, "factory produced an app with no routes"


def test_factory_is_callable_with_no_arguments():
    """`uvicorn module:create_app --factory` calls it with zero arguments.

    If `create_app` ever gains a required parameter, the launch string breaks
    and nothing else in the suite would notice — every other caller passes
    services explicitly.
    """
    import inspect
    from backend.server import create_app
    sig = inspect.signature(create_app)
    required = [p for p in sig.parameters.values()
                if p.default is inspect.Parameter.empty
                and p.kind not in (p.VAR_POSITIONAL, p.VAR_KEYWORD)]
    assert not required, (
        f"create_app() has required parameter(s) {[p.name for p in required]}, "
        f"so `--factory` cannot call it")


def test_launch_strings_are_consistent_across_the_repo():
    """All three launch strings must name the same target.

    They are the same string in three places (start.py's execvp, start.py's
    printed tip, docs/FIRST_RUN.md, which took QUICKSTART.md in at W11c). J.2 edits all three; this fails if one is
    missed, which would leave the operator a documented command that no
    longer works — the defect class H.4 corrected in CLAUDE.md.
    """
    start = (ROOT / "start.py").read_text(encoding="utf-8")
    quick = (ROOT / "docs" / "FIRST_RUN.md").read_text(encoding="utf-8")       # W11c: QUICKSTART.md merged into it

    import re
    targets = set(re.findall(r"backend\.server:(\w+)", start + quick))
    assert len(targets) == 1, (
        f"launch strings disagree on the target: {sorted(targets)}. "
        f"All of start.py's execvp, start.py's printed tip and docs/FIRST_RUN.md "
        f"must name the same one.")

    target = targets.pop()
    if target == "create_app":
        assert "--factory" in start and "--factory" in quick, (
            "the factory target is used without --factory, which uvicorn "
            "will treat as an ASGI app object and reject")
