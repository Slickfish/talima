"""W10b (run/specs/W10b.md D1 to D5) — governed actions: what NEXUS's runtime sends, recorded beside the answers.

An action here is an outbound request NEXUS's own runtime makes on a person's confirmed behalf: the web search (a
turn with the person's consent, or `POST /search/execute`) and the seeder's fetches (`/kb/seed/*`). `recorded_get`
is the one place such a request is sent. It inserts the action's row before the request leaves and sends it only
if that insert committed; it writes the outcome once, before the caller sees the result. A record that cannot be
written stops the action (`ActionNotRecorded`), and the caller says so; nothing is retried behind its back.

What a row holds: the kind; the target (scheme, host and path, never the query string, which is in the hash); the
request's hash (the method, the URL and the parameters, canonical); each header that carries a key, by name and
the first 16 hex of its value's SHA-256, never the value; the response's status and the SHA-256 of its body as
received; the trigger (a turn's lineage id, or the route a person posted to); the accountable identity and how it
was established; and, once the outcome is written, the record hash over all of it.

Not actions, declared in the spec's establish: model calls (the turn's own record holds them), model probes (reads),
anchoring (`block_anchors` is its record), and the paths no production code calls.

The recorder owns the network. The modules that send actions (the search tool, the seeder) import no network
library and hold no client: they hold a `session()` (one connection pool, each `get` one recorded action) or call
`recorded_get`, and take the HTTP error classes from here. What holds that, and what it does not see, is
`tests/test_w10b_actions.py` T4 (a static reading) and `tests/test_w10b_network_trace.py` (a trace of every path
it drives).
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

# What the action modules catch, from here: they import no network library of their own.
HTTPError = httpx.HTTPError
HTTPStatusError = httpx.HTTPStatusError

# W10c — `intercepted`: a tool use the interceptor saw the model ask for and, later, the harness report a result
# for. The class says what NEXUS saw; it never says the tool ran.
EVIDENCE_CLASSES = ("runtime", "client_reported", "intercepted")
# W10c — `model_exchange`: the request the interceptor forwards to its upstream, its row written before it leaves.
RUNTIME_KINDS = ("web_search", "seed_conceptnet", "seed_wikidata", "model_exchange")
# W10c — `incomplete`: an exchange whose response was cut, or whose record could not be written.
STATES = ("requested", "completed", "failed", "incomplete")
# A header whose value is a credential: by its name, or by a name ending in a word that says so.
_KEY_HEADERS = {"authorization", "proxy-authorization", "cookie", "x-api-key", "api-key", "apikey"}
_KEY_SUFFIXES = ("-key", "-token", "-secret", "-auth", "-api-key")
# The fields the record hash is over, in this order (the hash takes them as one canonical object). The
# accountable identity enters as a salted commitment (W9's), never as its value, so an action can be proved to a
# stranger without naming the person (A-2): the gated receipt discloses the value and its salt.
HASHED_FIELDS = ("action_id", "kind", "evidence_class", "method", "target", "request_hash", "credentials_json",
                 "lineage_id", "trigger_route", "identity_commitment", "identity_mechanism", "signature_key_id",
                 "signature", "requested_at", "state", "status", "response_hash", "completed_at")


class ActionNotRecorded(RuntimeError):
    """`action_not_recorded: <why>` — the action's record could not be written, so its request was not sent; or
    its outcome could not be written, so its result is withheld."""


@dataclass(frozen=True)
class Trigger:
    """What an action is done for: a turn (`lineage_id`, the id its record will carry) or a route a person posted
    to (`route`), and the request's accountable identity with how it was established."""
    lineage_id: "str | None" = None
    route: "str | None" = None
    accountable_identity: "str | None" = None
    identity_mechanism: "str | None" = None


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("ascii")


def request_hash(method: str, url: str, params) -> str:
    pairs = sorted([str(k), str(v)] for k, v in (dict(params) if params else {}).items())
    return hashlib.sha256(canonical({"method": method.upper(), "url": url, "params": pairs})).hexdigest()


def target_of(url: str) -> str:
    parts = urlsplit(url)
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{(parts.hostname or '').lower()}{port}{parts.path or '/'}"


def is_key_header(name: str) -> bool:
    low = name.strip().lower()
    return low in _KEY_HEADERS or low.endswith(_KEY_SUFFIXES)


def credentials_of(headers) -> list:
    """Each header that carries a key, by name and fingerprint; never its value."""
    return credentials_of_pairs((dict(headers) if headers else {}).items())


def _name_text(name) -> str:
    return name.decode("latin-1") if isinstance(name, (bytes, bytearray)) else str(name)


def _value_bytes(value) -> bytes:
    """A header's value as the bytes it is on the wire: the interceptor holds them as bytes (a value need not be
    ASCII), W10b's own callers as text."""
    return bytes(value) if isinstance(value, (bytes, bytearray)) else str(value).encode("utf-8")


def credentials_of_pairs(pairs) -> list:
    """The same, over `(name, value)` pairs as a request carries them: a header sent twice is two entries."""
    out = {(_name_text(name).lower(), hashlib.sha256(_value_bytes(value)).hexdigest()[:16])
           for name, value in pairs if is_key_header(_name_text(name))}
    return [{"header": header, "fingerprint": fingerprint} for header, fingerprint in sorted(out)]


def record_hash(row: dict) -> str:
    return hashlib.sha256(canonical({k: row.get(k) for k in HASHED_FIELDS})).hexdigest()


def identity_fields(accountable_identity) -> dict:
    """The accountable identity with a fresh 32-byte salt and its commitment (W9's `commitment`)."""
    import secrets
    from backend.memory import commitments as _commitments
    salt = secrets.token_bytes(_commitments.SALT_BYTES)
    return {"accountable_identity": accountable_identity, "identity_salt": salt.hex(),
            "identity_commitment": _commitments.commitment(salt, accountable_identity).hex()}


def _merged_headers(client, headers) -> dict:
    merged = {}
    for name, value in (getattr(client, "headers", None) or {}).items():
        merged[str(name)] = str(value)
    for name, value in (headers or {}).items():
        merged[str(name)] = str(value)
    return merged


def _outcome(store, row: dict, *, state: str, status, body: "bytes | None", now) -> None:
    done = {**row, "state": state, "status": None if status is None else str(status),
            "response_hash": None if body is None else hashlib.sha256(body).hexdigest(),
            "completed_at": time.time() if now is None else now}
    try:
        written = store.action_outcome(row["action_id"], state=done["state"], status=done["status"],
                                       response_hash=done["response_hash"], completed_at=done["completed_at"],
                                       record_hash=record_hash(done))
    except Exception as e:
        raise ActionNotRecorded(f"action_not_recorded: the {row['kind']} action's outcome could not be written "
                                f"({type(e).__name__}); its result is withheld") from None
    if written != 1:
        raise ActionNotRecorded(f"action_not_recorded: the {row['kind']} action's outcome was not written; its "
                                "result is withheld")


def verify_sealed(store, chain) -> dict:
    """W10c — every action whose outcome is written, re-checked both ways. From the row: its fields hash to its
    record hash, and the block that sealed it holds that hash as the leaf beside its id. From the block: every
    leaf a block sealed is the hash its row has now, so a row rewritten after its seal and sealed again (its
    first leaf is still in its first block), erased or deleted is named. The same leaf in two blocks is not a
    failure (a seal whose marks could not be written was once appended again). `{checked, sealed, unsealed,
    failed}`; nothing is written."""
    every = [dict(r) for r in store.conn.execute("SELECT * FROM action_records").fetchall()]
    rows = [r for r in every if r["record_hash"] is not None]
    failed, sealed = [], 0
    for row in sorted(rows, key=lambda r: (r["requested_at"], r["action_id"])):
        if record_hash(row) != row["record_hash"]:
            failed.append({"action_id": row["action_id"], "reason": "its fields do not hash to its record hash"})
            continue
        if row["sealed_block"] is None:
            continue
        sealed += 1
        block = chain.get_block(int(row["sealed_block"]))
        seal = ((block.fingerprint or {}).get("action_seal") or {}) if block is not None else {}
        ids, leaves = seal.get("action_ids") or [], seal.get("leaves") or []
        if row["action_id"] not in ids or leaves[ids.index(row["action_id"])] != row["record_hash"]:
            failed.append({"action_id": row["action_id"], "reason": "the block that sealed it does not hold it"})
    now = {r["action_id"]: r["record_hash"] for r in every}
    named = {f["action_id"] for f in failed}
    for block in chain.all_blocks():
        seal = (block.fingerprint or {}).get("action_seal") or {}
        for action_id, leaf in zip(seal.get("action_ids") or [], seal.get("leaves") or []):
            if action_id in named or (action_id in now and now[action_id] == leaf):
                continue
            named.add(action_id)
            failed.append({"action_id": action_id, "reason": (
                f"block {block.index} sealed it and the store no longer holds it" if action_id not in now else
                f"block {block.index} sealed it with a hash its row does not have now")})
    return {"checked": len(rows), "sealed": sealed, "unsealed": len(rows) - sealed - sum(
        1 for f in failed if f["reason"].startswith("its fields")), "failed": failed}


_HEX = set("0123456789abcdef")


class ReportRefused(ValueError):
    """`<reason>: <why>` — a client's report that is not one; nothing was written."""


def record_reported(store, *, lineage_id: str, kind: str, target: str, request_hash: str, status: str,
                    accountable_identity: "str | None", identity_mechanism: "str | None",
                    now: "float | None" = None) -> dict:
    """W10b (D9) — an action a client says it took, stored `client_reported`: NEXUS did not see it happen and
    the record says so. Its kind, target and status are the client's words, kept as sent within their limits;
    its request hash is the client's, a SHA-256. Written through the recorder's two statements, so the one
    writer set holds; complete at once, and sealed like every other action."""
    kind, target, status = (kind or "").strip(), (target or "").strip(), (str(status) if status is not None else "")
    if not kind or len(kind) > 64:
        raise ReportRefused("bad_kind: an action's kind is 1 to 64 characters")
    if not target or len(target) > 512:
        raise ReportRefused("bad_target: an action's target is 1 to 512 characters")
    if len(request_hash or "") != 64 or not set(request_hash) <= _HEX:
        raise ReportRefused("bad_request_hash: a request hash is a SHA-256, 64 lower-case hex characters")
    if len(status) > 64:
        raise ReportRefused("bad_status: a status is at most 64 characters")
    row = {"action_id": str(uuid.uuid4()), "kind": kind, "evidence_class": "client_reported", "method": "reported",
           "target": target, "request_hash": request_hash, "credentials_json": "[]", "lineage_id": lineage_id,
           "trigger_route": "mcp:nexus_record_action", **identity_fields(accountable_identity),
           "identity_mechanism": identity_mechanism, "signature_key_id": None, "signature": None,
           "requested_at": time.time() if now is None else now, "state": "requested"}
    try:
        store.action_requested(row)
    except Exception as e:
        raise ActionNotRecorded(f"action_not_recorded: the reported action could not be written "
                                f"({type(e).__name__})") from None
    _outcome(store, row, state="completed", status=status, body=None, now=row["requested_at"])
    return store.action(row["action_id"])


async def recorded_get(store, *, kind: str, url: str, params=None, headers=None, timeout: float = 10.0,
                       trigger: Trigger, client=None, now: "float | None" = None):
    """Send one GET as a recorded runtime action and return its response; the one place an action is sent.

    The row is committed before the request leaves; the outcome (`completed` with the status and the body's hash,
    or `failed` with the error's class) is written before the response is returned. A network error is recorded
    and re-raised, so the caller's own handling runs as before."""
    if store is None or trigger is None:
        raise ActionNotRecorded(f"action_not_recorded: a {kind} request with no record to write was not sent")
    from backend.memory import http_signatures as _hs
    when = time.time() if now is None else now
    signed, send_url, send_params = None, url, params
    if _hs.mode() == "on":
        # W10b (D10) — signed before its row is written, so the row carries the signature it was sent with; the
        # URL signed is the URL sent, its query built once here.
        signer = getattr(store, "request_signer", None)
        send_url, send_params = str(httpx.URL(url, params=params)), None
        try:
            if signer is None:
                raise LookupError("no key signs requests here")
            signed = signer(method="GET", url=send_url, headers=headers or {}, now=when)
        except Exception as e:
            raise ActionNotRecorded(f"action_not_recorded: NEXUS_SIGN_REQUESTS=on and the {kind} request could "
                                    f"not be signed ({type(e).__name__}); it was not sent") from None
        headers = {**(headers or {}), "Signature-Input": signed["Signature-Input"], "Signature": signed["Signature"]}
    row = {"action_id": str(uuid.uuid4()), "kind": kind, "evidence_class": "runtime", "method": "GET",
           "target": target_of(url), "request_hash": request_hash("GET", url, params),
           "credentials_json": canonical(credentials_of(_merged_headers(client, headers))).decode("ascii"),
           "lineage_id": trigger.lineage_id, "trigger_route": trigger.route,
           **identity_fields(trigger.accountable_identity), "identity_mechanism": trigger.identity_mechanism,
           "signature_key_id": signed["key_id"] if signed else None, "signature": signed["signature"] if signed else None,
           "requested_at": when, "state": "requested"}
    try:
        store.action_requested(row)
    except Exception as e:
        raise ActionNotRecorded(f"action_not_recorded: the {kind} request's record could not be written "
                                f"({type(e).__name__}); it was not sent") from None
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout)
    try:
        try:
            response = await client.get(send_url, params=send_params, headers=headers or {}, timeout=timeout)
        except Exception as e:
            _outcome(store, row, state="failed", status=type(e).__name__, body=None, now=now)
            raise
        _outcome(store, row, state="completed", status=response.status_code, body=response.content, now=now)
        return response
    finally:
        if own:
            await client.aclose()


@dataclass(frozen=True)
class Session:
    """A recorded client: each `get` is one runtime action sent by `recorded_get`, on one connection pool. What
    an action module holds instead of a network client."""
    _store: object
    _trigger: Trigger
    _client: object
    _timeout: float

    async def get(self, url: str, *, kind: str, params=None, headers=None, timeout: "float | None" = None):
        return await recorded_get(self._store, kind=kind, url=url, params=params, headers=headers,
                                  timeout=self._timeout if timeout is None else timeout, trigger=self._trigger,
                                  client=self._client)


@contextlib.asynccontextmanager
async def session(*, store, trigger: Trigger, headers=None, timeout: float = 10.0):
    """`async with session(store=..., trigger=...) as client:` — a `Session` over one client this module opens and
    closes; its default headers are the client's, so they are in each row's credentials."""
    async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
        yield Session(store, trigger, client, timeout)
