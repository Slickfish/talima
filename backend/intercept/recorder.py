"""W10c (run/specs/W10c.md S3 to S6) — the interceptor's record: what is written, when, and in which transaction.

Two writes an exchange, each one transaction on the lineage store:

  before the request leaves   `exchange_requested`: the exchange's row (a runtime action, kind `model_exchange`,
                              state `requested`), the request's bytes (kept, W0b) and the outcome of every earlier
                              tool use whose result this request reports. If it does not commit, nothing is sent.
  when the response is whole  `exchange_recorded`: the lineage record (scope `intercepted`), a row for each tool use
                              the response asks for (class `intercepted`, state `requested`), and the exchange's
                              outcome. If it does not commit, none of the three is written, the exchange is marked
                              `incomplete` in a write of its own, and the caller ends the stream with an error.

The store is the interceptor's own: a folder holding `kb.db`, `archive.db`, the instance key and a marker file made
before the store is. A folder that holds a store and no marker is some other mode's, and is refused. The
single-writer lock is taken before the first connect (W2). Nothing here imports the memory engine
(tests/test_w10c_standalone.py): the record, the chain, the seal, the key, the store and the lock.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote

from backend import store_lock
from backend.intercept import protocols
from backend.intercept.config import ConfigRefused
from backend.memory import actions as _actions
from backend.memory import commitments as _commitments
from backend.memory import credentials as _credentials
from backend.memory import instance_key as _ik
from backend.memory import seal as _seal
from backend.memory.chain import NexusArchiveChain
from backend.memory.lineage import (INTERCEPTED, REQUEST_ENCODING_HTTP_BODY, LineageStore, verify_sealed_lineage)

logger = logging.getLogger(__name__)

LOCK_MODE = "intercept"
MARKER = "talima_intercept.json"
MARKER_KIND = "talima-intercept"
EXCHANGE_KIND = "model_exchange"
TOOL_USE_KIND = "tool_use"
UPSTREAM_REPORTED = "upstream-reported:"
TOOL_USES_NOTE = "tool_uses"
STOPPED = "outcome_not_written: this exchange's outcome was not written before the process ended"
# Why each replay-capture field is NULL on an intercepted record (W0b's fields describe a model NEXUS ran).
CAPTURE_ABSENT = {f: "not observable: the upstream's model ran outside this process"
                  for f in ("inference_seed", "model_digest", "inference_config_hash", "environment_hash")}


class NotRecorded(RuntimeError):
    """`<reason>: <why>` — a write the exchange needs did not commit."""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tool_target(tool_use_id: str, name: str) -> str:
    """A tool-use row's target: the id the model gave (percent-quoted, so it holds no colon) and the tool's name."""
    return f"{TOOL_USE_KIND}:{quote(protocols._clip(tool_use_id), safe='')}:{protocols._clip(name)}"


def _as_asked(row: dict) -> str:
    """The hash of a tool use's whole row as it stood when the model asked: every field an action's record hash
    is over, with no outcome yet. A row that has since been answered still gives this, its outcome set aside."""
    return _actions.record_hash({**row, "state": "requested", "status": None, "response_hash": None,
                                 "completed_at": None})


def _named(uses) -> str:
    """The tool uses a record names, as it names them: each row's target, its request hash, and the hash of the
    whole row as it was asked (`_as_asked`), sorted, as JSON."""
    return json.dumps(sorted([u["target"], u["request_hash"], _as_asked(u)] for u in uses), separators=(",", ":"))


def _status(result) -> str:
    return "reported_error" if result.is_error else "reported"


def _row_problem(row: dict) -> "str | None":
    """What of a tool use's row the hash of it as asked does not hold: that its accountable identity and salt
    open the commitment the hash is over; that a row no longer waiting carries the hash of itself; and that a row
    still waiting carries neither a hash nor a seal mark (it has no outcome to hash, and nothing sealed it)."""
    if not _opens(row):
        return "its accountable identity and salt do not open its identity commitment"
    if row["state"] != "requested" and row["record_hash"] != _actions.record_hash(row):
        return "it has an outcome and not the hash of its own row"
    if row["state"] == "requested" and (row["record_hash"] is not None or row["sealed_block"] is not None):
        return "it is waiting for its outcome and carries a record hash or a seal mark"
    return None


def _opens(row: dict) -> bool:
    """Whether an action's accountable identity and salt open the identity commitment its record hash is over."""
    try:
        opened = _commitments.commitment(bytes.fromhex(row["identity_salt"] or ""),
                                         row["accountable_identity"]).hex()
    except (ValueError, TypeError):
        return False
    return opened == row["identity_commitment"]


def tool_id_of(target: str) -> "str | None":
    parts = (target or "").split(":", 2)
    return unquote(parts[1]) if len(parts) == 3 and parts[0] == TOOL_USE_KIND else None


@dataclass
class Recorder:
    lineage: LineageStore
    chain: NexusArchiveChain
    key_path: Path
    lock: object
    root: Path

    # ---------------------------------------------------------------- opening and closing

    @classmethod
    def open_at(cls, root: "Path | str") -> "Recorder":
        """Open the interceptor's store at `root`, making it when the folder holds none. Refused by name when the
        folder holds a store this module did not make; `StoreLocked` when another process holds it."""
        root = Path(root)
        kb, marker = root / "kb.db", root / MARKER
        if kb.exists() and not marker.exists():
            raise ConfigRefused(f"not_the_interceptors_store: {root} holds a store and no {MARKER}; the interceptor "
                                "records only into a store it made")
        root.mkdir(parents=True, exist_ok=True)
        lock = store_lock.acquire(kb, mode=LOCK_MODE)            # before the first connect: a refused process makes nothing
        try:
            if not marker.exists():
                marker.write_text(json.dumps({"kind": MARKER_KIND, "made_at": time.time()}) + "\n", encoding="utf-8")
            lineage = LineageStore(kb)
            chain = NexusArchiveChain(root / "archive.db")
            key_path = root / _ik.KEY_FILE
            chain.signer = _ik.signer_for(key_path, chain)
            chain.identity_source = _ik.identity_source_for(key_path, chain)
        except BaseException:
            lock.release()
            raise
        return cls(lineage=lineage, chain=chain, key_path=key_path, lock=lock, root=root)

    def close(self) -> None:
        for closing in (self.lineage.close, self.chain.close, self.lock.release):
            try:
                closing()
            except Exception as e:                       # the others still close
                logger.warning("closing the interceptor's store: %s: %s", type(e).__name__, e)

    # ---------------------------------------------------------------- the two writes

    def _identity(self, identity: "str | None", mechanism: "str | None") -> dict:
        return {**_actions.identity_fields(identity), "identity_mechanism": mechanism}

    def exchange_requested(self, *, lineage_id: str, protocol: str, path: str, url: str, harness_body: bytes,
                           forwarded_body: bytes, headers: list, identity: "str | None",
                           mechanism: "str | None", results: tuple = (), now: "float | None" = None) -> dict:
        """The exchange's row, the request's bytes and the tool results it reports, committed together before the
        request leaves. Returns the row; raises `NotRecorded` and writes nothing when it cannot."""
        when = time.time() if now is None else now
        row = {"action_id": str(uuid.uuid4()), "kind": EXCHANGE_KIND, "evidence_class": "runtime", "method": "POST",
               "target": _actions.target_of(url), "request_hash": _sha(forwarded_body),
               "credentials_json": _actions.canonical(_actions.credentials_of_pairs(headers)).decode("ascii"),
               "lineage_id": lineage_id, "trigger_route": f"intercept:{path}", **self._identity(identity, mechanism),
               "signature_key_id": None, "signature": None, "requested_at": when, "state": "requested"}
        try:
            reported = self._reported(protocol, results, when, row["request_hash"])
            not_kept = self.lineage.exchange_requested(row, request_hash=_sha(harness_body),
                                                       request_body=harness_body,
                                                       encoding=REQUEST_ENCODING_HTTP_BODY, outcomes=reported)
        except Exception as e:
            raise NotRecorded(f"talima_request_not_recorded: the request's record could not be written "
                              f"({type(e).__name__}); it was not sent") from None
        return {**row, "request_note": not_kept, "results_linked": [action_id for action_id, _o, _h in reported]}

    def _reported(self, protocol: str, results: tuple, when: float, request_hash: str) -> list:
        """The outcome of each waiting tool use a request reports on: `(action_id, outcome fields, record hash)`.

        A harness sends its whole history with every request, in whatever shape it keeps it. So the rule reads no
        shape. An ask is one hash: the protocol, the whole id, the name and what was asked, numbers by value. A
        result the request gives for an ask is a REPORT when no assistant message follows it (it is current), or
        when it answers the last time the request holds that ask. A report goes to a tool use still waiting under
        that hash, the ones asked last first. Never by the id alone.

        Two things are taken for something else. A report that an assistant message follows is history sent
        again when the store already holds as many tool uses with that very ask and result as the request holds
        answers of them. And a tool use asked in reply to this very request (the same forwarded bytes) is never a
        candidate: a request sent again cannot report on what the model said in reply to it.

        What this cannot tell apart, it does not pretend to (`docs/INTERCEPTOR.md`, "What a link does not say"):
        two tool uses with one id, name and input are one ask, whichever conversation asked."""
        reports = {}                                     # ask hash -> its reports, in the request's order
        for r in results:
            if r.ask_hash and (r.current or r.latest):
                reports.setdefault(r.ask_hash, []).append(r)
        if not reports:
            return []
        waiting, replied_to = {}, {}
        for row in self.lineage.actions_in_state(evidence_class="intercepted", kind=TOOL_USE_KIND, state="requested"):
            if row["method"] != protocol or row["request_hash"] not in reports:
                continue
            if row["lineage_id"] not in replied_to:      # the request the asking exchange was the reply to
                replied_to[row["lineage_id"]] = {a["request_hash"] for a in self.lineage.actions_for(row["lineage_id"])
                                                 if a["kind"] == EXCHANGE_KIND}
            if request_hash not in replied_to[row["lineage_id"]]:
                waiting.setdefault(row["request_hash"], []).append(row)          # oldest first
        out = []
        for ask, given in sorted(reports.items()):
            rows = waiting.get(ask) or []
            if rows and not given[-1].current and self._is_history(ask, given[-1], results):
                continue
            count = min(len(rows), len(given))
            for row, result in zip(rows[len(rows) - count:], given[len(given) - count:]):
                done = {**row, "state": "completed", "status": _status(result), "response_hash": result.response_hash,
                        "completed_at": when}
                out.append((row["action_id"],
                            {k: done[k] for k in ("state", "status", "response_hash", "completed_at")},
                            _actions.record_hash(done)))
        return sorted(out, key=lambda o: o[0])

    def _is_history(self, ask: str, report, results: tuple) -> bool:
        """Whether a report an assistant message follows is one the store already holds: it holds as many
        completed tool uses with this ask, result and status as the request holds answers of them."""
        answers = sum(1 for r in results if r.ask_hash == ask and r.response_hash == report.response_hash
                      and r.is_error == report.is_error)
        held = self.lineage.conn.execute(
            "SELECT COUNT(*) AS n FROM action_records WHERE evidence_class = ? AND kind = ? AND state = ? "
            "AND request_hash = ? AND status = ? AND response_hash = ?",
            ("intercepted", TOOL_USE_KIND, "completed", ask, _status(report), report.response_hash)).fetchone()
        return int(held["n"]) >= answers

    def exchange_recorded(self, row: dict, *, protocol: str, path: str, harness_hash: str, response: bytes,
                          facts: "protocols.ResponseFacts", state: str, status: str, notes: "dict | None" = None,
                          context: "dict | None" = None, injected: "list | None" = None,
                          now: "float | None" = None):
        """The exchange's lineage record, a row for each tool use the response asks for, and the exchange's
        outcome: one transaction. Raises `NotRecorded` having written none of them."""
        when = time.time() if now is None else now
        uses = []

        def with_the_record() -> None:
            for use in uses:
                self.lineage.action_requested(use, commit=False)
            written = self.lineage.action_outcome(row["action_id"], state=done["state"], status=done["status"],
                                                  response_hash=done["response_hash"],
                                                  completed_at=done["completed_at"],
                                                  record_hash=_actions.record_hash(done), commit=False)
            if written != 1:
                raise NotRecorded("the exchange's row was not `requested` when its outcome was written")

        try:
            done = {**row, "state": state, "status": status, "response_hash": _sha(response), "completed_at": when}
            uses += [{"action_id": str(uuid.uuid4()), "kind": TOOL_USE_KIND, "evidence_class": "intercepted",
                      "method": protocol, "target": tool_target(use.tool_use_id, use.name),
                      "request_hash": use.request_hash, "credentials_json": "[]", "lineage_id": row["lineage_id"],
                      "trigger_route": f"intercept:{path}",
                      **self._identity(row["accountable_identity"], row["identity_mechanism"]),
                      "signature_key_id": None, "signature": None, "requested_at": when, "state": "requested"}
                     for use in facts.uses]
            capture_notes = {**CAPTURE_ABSENT, **{str(k): str(v) for k, v in (notes or {}).items()}}
            if uses:
                # The record names every tool use its response asked for (S6): a tool use has no outcome, so no
                # seal of its own, until a harness reports one, and the sealed record is what holds it meanwhile.
                capture_notes[TOOL_USES_NOTE] = _named(uses)
            return self.lineage.record_turn(
                query="", injected=list(injected or []), contradictions=[],
                model_id=UPSTREAM_REPORTED + (facts.model or protocols.UNKNOWN_MODEL),
                response_text="", response_bytes=response, prompt_prefix="", request_hash=harness_hash,
                context=context, accountable_identity=row["accountable_identity"],
                identity_mechanism=row["identity_mechanism"], learning_mode="frozen", record_scope=INTERCEPTED,
                capture_notes=capture_notes, lineage_id=row["lineage_id"], in_transaction=with_the_record)
        except Exception as e:
            raise NotRecorded(f"talima_response_not_recorded: the response's record could not be written "
                              f"({type(e).__name__})") from None

    def exchange_closed(self, row: dict, *, state: str, status: str, response: "bytes | None" = None,
                        now: "float | None" = None) -> bool:
        """The exchange's outcome alone, when no record goes with it (the upstream was never reached; the record
        could not be written): whether it was written. Never raises: the caller is already telling the harness."""
        done = {**row, "state": state, "status": status,
                "response_hash": None if response is None else _sha(response),
                "completed_at": time.time() if now is None else now}
        try:
            return self.lineage.action_outcome(row["action_id"], state=done["state"], status=done["status"],
                                               response_hash=done["response_hash"],
                                               completed_at=done["completed_at"],
                                               record_hash=_actions.record_hash(done)) == 1
        except Exception as e:
            logger.error("exchange %s: its outcome (%s) could not be written: %s", row["lineage_id"], state,
                         type(e).__name__)
            return False

    def still_owed(self, owed: list) -> list:
        """`owed` is `(row, outcome)` for exchanges whose outcome could not be written when they ended. Each is
        written now if its row still waits for one; what is returned is what is still owed after that. A row that
        has its outcome, or is gone, is owed nothing (the write changes only a row still `requested`). When the
        store cannot even be read, everything is still owed."""
        try:
            waiting = {row["action_id"] for row in self.lineage.actions_in_state(
                evidence_class="runtime", kind=EXCHANGE_KIND, state="requested")}
        except Exception as e:
            logger.warning("the exchanges still owed an outcome could not be read: %s", type(e).__name__)
            return list(owed)
        return [(row, outcome) for row, outcome in owed
                if row["action_id"] in waiting and not self.exchange_closed(row, **outcome)]

    def sweep_unfinished(self, now: "float | None" = None) -> int:
        """Exchanges a process left `requested` when it stopped: marked `incomplete`, by name."""
        rows = self.lineage.actions_in_state(evidence_class="runtime", kind=EXCHANGE_KIND, state="requested")
        return sum(1 for row in rows if self.exchange_closed(row, state="incomplete", status=STOPPED, now=now))

    # ---------------------------------------------------------------- the seal, and what the command line reads

    def seal(self):
        """One governance block over everything unsealed, or None. Raises what the append raises."""
        return _seal.seal_governance(self.chain, self.lineage)

    def pending(self) -> int:
        return len(self.lineage.unsealed()) + len(self.lineage.unsealed_actions())

    def listing(self, *, limit: int = 20) -> list:
        """The recorded exchanges, newest first: ids, times, states and hashes; no content."""
        records = {r.lineage_id: r for r in self.lineage.recent(limit)}
        rows = [dict(r) for r in self.lineage.conn.execute(
            "SELECT * FROM action_records WHERE kind = ?", (EXCHANGE_KIND,)).fetchall()]
        rows.sort(key=lambda r: (r["requested_at"], r["action_id"]), reverse=True)
        out = []
        for row in rows[:limit]:
            rec = records.get(row["lineage_id"]) or self.lineage.get(row["lineage_id"])
            uses = [a for a in self.lineage.actions_for(row["lineage_id"]) if a["kind"] == TOOL_USE_KIND]
            out.append({"lineage_id": row["lineage_id"], "requested_at": row["requested_at"], "state": row["state"],
                        "status": row["status"], "route": row["trigger_route"], "exchange_action_id": row["action_id"],
                        "request_hash": row["request_hash"], "response_hash": row["response_hash"],
                        "recorded": rec is not None, "model_id": rec.model_id if rec else None,
                        "sealed_block": rec.sealed_block if rec else None,
                        "tool_uses": [{"action_id": u["action_id"], "evidence_class": u["evidence_class"],
                                       "target": u["target"], "state": u["state"], "status": u["status"]}
                                      for u in uses]})
        return out

    def credential(self, lineage_id: str, *, disclose=()) -> dict:
        return _credentials.build_credential(self.lineage, self.chain, lineage_id, key_path=self.key_path,
                                             disclose=disclose)

    def identity(self) -> dict:
        """The instance's public identity: its current key and every key the chain has announced. What a verifier
        is given to pin the signatures to (`talima-verify --identity`)."""
        return _ik.identity_document(_ik.load_or_create(self.key_path, chain=self.chain), self.chain)

    def receipt(self, action_id: str) -> dict:
        return _credentials.action_receipt(self.lineage, self.chain, action_id, key_path=self.key_path)

    def chain_document(self) -> dict:
        """W11d — the store's chain as one document (docs/TALIMA_SPEC.md, section 11): hashes and signatures, no
        content. `talima-verify --chain` checks its blocks, their links and their seals."""
        return _credentials.chain_document(self.chain, key_path=self.key_path)

    def tool_uses_named(self) -> dict:
        """Every tool use against the record that names it: `{records, named, failed}`. A tool use waits for a
        harness's report before it has an outcome and a seal of its own; until then, and after, the sealed record
        of the exchange names it by the hash of its whole row as it was asked, so a row removed or added, or
        changed in any hashed field but its outcome, is seen here. Four columns are not in that hash. The
        accountable identity and its salt: the hash holds their commitment, and they are held by opening it. The
        row's own record hash and seal mark: a waiting row has neither, and one that carries either is named. A
        row that is no longer waiting must carry the hash of itself (the recorder writes it with the outcome, and
        the next seal takes it): an outcome with no hash, or another's, is named. From its first seal an outcome
        is held by that seal's leaf (`actions.verify_sealed` reads from the block to the row too); until then
        it is, like every unsealed write, the store's own word. Nothing is written."""
        held, records, named, failed = {}, 0, 0, []
        for row in self.lineage.conn.execute("SELECT * FROM action_records WHERE evidence_class = ? AND kind = ?",
                                             ("intercepted", TOOL_USE_KIND)).fetchall():
            row = dict(row)
            held.setdefault(row["lineage_id"], []).append([row["target"], row["request_hash"], _as_asked(row)])
            why = _row_problem(row)
            if why:
                failed.append({"lineage_id": row["lineage_id"], "action_id": row["action_id"], "reason": why,
                               "named": 0, "held": 1})
        for row in self.lineage.conn.execute("SELECT lineage_id, capture_notes FROM lineage_records "
                                             "WHERE record_scope = ?", (INTERCEPTED,)).fetchall():
            records += 1
            try:
                notes = json.loads(row["capture_notes"] or "{}")
                names = json.loads(notes.get(TOOL_USES_NOTE) or "[]")
                if not (isinstance(names, list) and all(isinstance(n, list) and len(n) == 3
                                                        and all(isinstance(x, str) for x in n) for n in names)):
                    names = None
            except (ValueError, AttributeError, TypeError, RecursionError):
                names = None                     # a note that cannot be read names nothing: the record is named
            rows = sorted(held.pop(row["lineage_id"], []))
            if names != rows:
                failed.append({"lineage_id": row["lineage_id"], "reason": "the tool uses its record names are not "
                               "the rows the store holds", "named": len(names or []), "held": len(rows)})
            named += len(names or [])
        for lineage_id in sorted(held, key=str):
            failed.append({"lineage_id": lineage_id, "reason": "tool-use rows no record names", "named": 0,
                           "held": len(held[lineage_id])})
        return {"records": records, "named": named, "failed": failed}

    def identities_open(self) -> dict:
        """Every other action's accountable identity and salt against its commitment: `{checked, failed}`. A
        tool use's are opened with its row (`tool_uses_named`); these are the exchanges' own rows."""
        rows = [dict(r) for r in self.lineage.conn.execute(
            "SELECT * FROM action_records WHERE NOT (evidence_class = ? AND kind = ?)",
            ("intercepted", TOOL_USE_KIND)).fetchall()]
        failed = [{"action_id": row["action_id"], "lineage_id": row["lineage_id"],
                   "reason": "its accountable identity and salt do not open its identity commitment"}
                  for row in sorted(rows, key=lambda r: (r["requested_at"], r["action_id"])) if not _opens(row)]
        return {"checked": len(rows), "failed": failed}

    def verify(self) -> dict:
        chain = self.chain.verify_chain()
        records = verify_sealed_lineage(self.lineage, self.chain)
        acts = _actions.verify_sealed(self.lineage, self.chain)
        uses = self.tool_uses_named()
        identities = self.identities_open()
        ok = bool(chain.valid) and records.get("verdict") == "intact" and not acts["failed"] \
            and not uses["failed"] and not identities["failed"]
        return {"ok": ok, "chain": chain.to_dict(), "records": records, "actions": acts, "tool_uses": uses,
                "identities": identities}
