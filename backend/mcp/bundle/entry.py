"""W2 — the bundle's entry point: the script PyInstaller builds `nexus-mcp`
from (`scripts/build_bundle.py`). It runs `python -m backend.mcp`
(`backend/mcp/__main__.py`) as it is, and adds one thing, dormant unless
asked for: when NEXUS_AUDIT_NETWORK names a file, every connect, name
lookup, send, bind and subprocess call the process makes is appended to that
file, one JSON line each, through Python's audit hooks (PEP 578), after a
first line saying the hook is installed. Each line says whether the call
was made inside the standard library's own socketpair (found on the call's
stack, by the function's name and its module being `socket` itself, never
by the address: W0's rule, tests/test_mcp2_build.py), which is how Python
on Windows makes the event loop's socket pair, whether it was urllib3's
import-time IPv6 probe, and whether it was the standard library's own `ver`
query (`platform._syscmd_ver`: Python 3.11 on Windows reads the OS version
by running `cmd /c ver`, so any `platform.platform()` spawns a shell once).
Nothing is refused or changed. The bundle smoke sets it and reads the file
(`scripts/bundle_smoke.py`, S3); unset, no hook is installed. A native
library's own sockets do not pass through the hooks."""
import json
import os
import socket
import sys

AUDIT_SETTING = "NEXUS_AUDIT_NETWORK"
AUDITED = frozenset({"socket.connect", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyname_ex",
                     "socket.gethostbyaddr", "socket.getnameinfo", "socket.sendto", "socket.sendmsg",
                     "socket.bind", "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
                     "os.startfile"})


def _plain(value):
    """A JSON value for an audit argument: addresses stay tuples (as lists),
    anything else is its repr, cut short."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    if isinstance(value, bytes):
        return value.decode("ascii", "replace")
    return repr(value)[:200]


STARTED = "nexus.audit_started"
# The standard library's socketpair under its two names (3.11.17 and later
# define `_fallback_socketpair` and bind `socketpair` to it on Windows).
SOCKETPAIR_FUNCTIONS = ("socketpair", "_fallback_socketpair")


# urllib3's import-time IPv6 probe (`HAS_IPV6 = _has_ipv6("::1")`): a bind to
# ::1 on port 0, closed at once, made whenever the MCP process imports
# urllib3 (langchain_core, then requests) on a host with IPv6. M.3's rule for
# it (tests/test_import_purity.py); W2's delta check, D1.
IPV6_PROBE = ("urllib3.util.connection", ("_has_ipv6",))

# The standard library's OS-version query on Windows: `platform._syscmd_ver`
# runs `ver` through a shell (CPython 3.11; 3.12 asks WMI instead), so the
# first `platform.platform()` in a process (the record's environment capture,
# W0b) is one `subprocess.Popen` the audit hook sees. Found by CI's first
# Windows run of the smoke's `--source` form; the frozen binary's run-time
# hooks had made the query before the hook was installed, so the binary passed.
VER_QUERY = ("platform", ("_syscmd_ver",))


def _on_stack(frame, module: str, functions) -> bool:
    """True iff the stack from `frame` outward passes through one of
    `functions` of `module` itself: by the module's own namespace, never by
    name or file, so a function so named elsewhere, or code run with a
    borrowed module name, is not it. (Not W0's file match: in the PyInstaller
    build a module's code names a relative file, so the file never matches,
    while the namespace does.)"""
    target = sys.modules.get(module)
    if target is None:
        return False
    names = vars(target)
    while frame is not None:
        if frame.f_code.co_name in functions and frame.f_globals is names:
            return True
        frame = frame.f_back
    return False


def in_stdlib_socketpair(frame) -> bool:
    return _on_stack(frame, "socket", SOCKETPAIR_FUNCTIONS)


def in_urllib3_ipv6_probe(frame) -> bool:
    return _on_stack(frame, *IPV6_PROBE)


def in_stdlib_ver_query(frame) -> bool:
    return _on_stack(frame, *VER_QUERY)


def audit_network(path: str) -> None:
    """Record every audited event to `path`, after a first line saying the
    hook is installed, so a file with no event in it means "nothing was
    called", never "nothing was watching". The hook never raises: a record
    it cannot write is lost, never a call refused."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": STARTED, "args": [os.getpid()]}) + "\n")

    def hook(event, args):
        if event in AUDITED:
            # Every audited event is written, whatever its stack: a call with
            # no Python frame below it (a thread started on a C function) has
            # none, which is not a reason to drop it (W2's delta check, D2).
            try:
                caller = sys._getframe(0).f_back
                marks = {"socketpair": in_stdlib_socketpair(caller), "ipv6_probe": in_urllib3_ipv6_probe(caller),
                         "ver_query": in_stdlib_ver_query(caller)}
            except Exception:
                marks = {"socketpair": False, "ipv6_probe": False, "ver_query": False}
            try:
                record = {"event": event, "args": [_plain(a) for a in args], **marks}
            except Exception:
                record = {"event": event, "args": ["(not representable)"], **marks}
            try:
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record) + "\n")
            except Exception:
                pass
    sys.addaudithook(hook)


if __name__ == "__main__":
    if os.environ.get(AUDIT_SETTING):
        audit_network(os.environ[AUDIT_SETTING])
    from backend.mcp.__main__ import main
    sys.exit(main())
