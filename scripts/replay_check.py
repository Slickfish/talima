"""W0b — is the replay capture sufficient? Re-send kept requests, compare.

    python scripts/replay_check.py --db data/kb.db [--last 20] [--confirm]
                                   [--report-dir reports] [--capture PATH]

Instance-side and read-only (RUN_CHARTER.md law 15). The FIRST line printed
is the database path. The store is opened `mode=ro` with an authorizer that
refuses every write, and nothing is written to it. The one file written is
`<report-dir>/replay_check_<UTC date>.md`. It REFUSES TO RUN WHILE NEXUS IS
SERVING (something answers on NEXUS_HOST:NEXUS_PORT), because concurrent
requests change the daemon's batching and a divergence measured beside them
would mean nothing.

Without `--confirm` it is a dry run: each of the last N records is
classified and nothing is sent. With `--confirm`, the daemon's `/api/tags` is
read once. Each replayable record's kept request is then re-sent with its
recorded seed and options (the body rebuilt by `replay_capture.wire_body`),
and the reply is parsed the way the record's turn parsed it:

  reproduced      the reply's content stream hashes to the record's
                  `response_hash` and its separated answer to `answer_hash`
  diverged        either differs. The offset of the first differing character
                  is printed where the answer's text was captured (AO's
                  citation capture, which stores the answer bound to its
                  hash), and declared unmeasurable elsewhere: the record keeps
                  hashes, never text
  not replayable  counted apart, with the reason, NEVER folded into a rate:
                  the record does not verify (its own hash); written before
                  the capture; an `mcp_block` record (the client's model ran
                  outside NEXUS); no kept request, or a corrupted or missing
                  copy; no seed; no options, or an options row that does not
                  hash to the record's key, is not text, or names another
                  model; no digest; a provider other than Ollama (W3: a
                  stub record, whose provider ran no model, and an api
                  record, openai or anthropic, are classified by that
                  first; LM Studio's by its missing digest); the tag
                  moved to other weights (reported, never replayed); the tag
                  not installed; the daemon refused the request

Exit codes: 0 done, whatever the counts; 2 no database or bad arguments;
3 refused because NEXUS is serving; 5 the daemon could not be reached
(`--confirm` only: the dry run contacts nothing).
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import socket
import sqlite3
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts._console import force_utf8_stdio                    # noqa: E402
from scripts._env import load_env                                # noqa: E402
from scripts._readonly import (column_names, open_read_only, read_only_how, store_exists,  # noqa: E402
                               store_label, table_exists)

REPRODUCED, DIVERGED, NOT_REPLAYABLE = "reproduced", "diverged", "not replayable"
CAPTURE_COLUMNS = ("inference_seed", "model_digest", "inference_config_hash", "environment_hash",
                   "capture_notes")
REPLAYABLE_PROVIDERS = ("ollama",)
# W3 — providers whose records this check never re-sends, classified by the
# provider FIRST (the options row names it), before the incidental reasons a
# record under them also has (no seed, no digest): the stub ran no model, and
# an API names a model, never the weights behind it.
NEVER_RESENT = {
    "stub": "LLM_BACKEND=stub: the stub provider ran no model; there is nothing to re-run (its answer is fixed)",
    "openai": ("provider 'openai': this check re-sends to Ollama only (the one provider that names the weights "
               "it serves; an API names a model, never its weights)"),
    "anthropic": ("provider 'anthropic': this check re-sends to Ollama only (the one provider that names the "
                  "weights it serves; an API names a model, never its weights)"),
}
EXIT_OK, EXIT_ARGS, EXIT_SERVING, EXIT_UNREACHABLE = 0, 2, 3, 5


class DaemonUnreachable(RuntimeError):
    """The model daemon did not answer (--confirm only)."""


# ---------------------------------------------------------------------------
# The record, read
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    lineage_id: str
    ts: float
    model_id: str
    record_scope: str | None
    response_hash: str
    answer_hash: str | None
    request_hash: str | None
    seed: int | None
    digest: str | None
    config: dict | None
    notes: dict
    messages: list | None = None
    reason: str | None = None            # set when not replayable

    @property
    def replayable(self) -> bool:
        return self.reason is None


def _columns(conn, table: str) -> set[str]:
    return column_names(conn, table)


def candidates(conn: sqlite3.Connection, last: int) -> list[Candidate]:
    """The last `last` records, newest first, each classified. A store
    written before W0b has no capture columns: every record reads as written
    before the capture, never as an error."""
    from backend.memory.lineage import LineageStore, _hash_payload_from_row   # pure: no store is opened
    cols = _columns(conn, "lineage_records")
    captured = all(c in cols for c in CAPTURE_COLUMNS)
    rows = conn.execute("SELECT * FROM lineage_records ORDER BY ts DESC, lineage_id DESC LIMIT ?",
                        (max(1, int(last)),)).fetchall()
    have_bodies = table_exists(conn, "request_bodies")
    out = []
    for r in rows:
        get = (lambda k: r[k] if k in r.keys() else None)
        notes = json.loads(get("capture_notes")) if get("capture_notes") else {}
        c = Candidate(lineage_id=r["lineage_id"], ts=r["ts"], model_id=r["model_id"],
                      record_scope=get("record_scope"), response_hash=r["response_hash"],
                      answer_hash=get("answer_hash"), request_hash=get("request_hash"),
                      seed=get("inference_seed"), digest=get("model_digest"), config=None, notes=notes)
        # Nothing the record keys is trusted before the record itself verifies:
        # an edited options row re-keyed consistently, with the record's key
        # moved to match, is a tampered record (part 5).
        intact = LineageStore.compute_record_hash(_hash_payload_from_row(r)) == r["record_hash"]
        c.reason = ("the record does not verify: its fields do not hash to its record_hash" if not intact else
                    _classify(conn, c, captured=captured and any(get(k) is not None for k in CAPTURE_COLUMNS),
                              config_hash=get("inference_config_hash"), have_bodies=have_bodies))
        out.append(c)
    return out


def _classify(conn, c: Candidate, *, captured: bool, config_hash: str | None, have_bodies: bool) -> str | None:
    """None when the record can be re-sent; otherwise why it cannot."""
    if c.record_scope == "mcp_block":
        return "mcp_block: the client's model ran outside NEXUS"
    if not captured:
        return "written before the replay capture (W0b)"
    if not c.request_hash:
        return "no request_hash on the record"
    row = (conn.execute("SELECT body FROM request_bodies WHERE request_hash = ?",
                        (c.request_hash,)).fetchone() if have_bodies else None)
    if row is None:
        if c.notes.get("request_body"):
            return f"request not kept ({c.notes['request_body']})"
        return ("the kept copy is missing, and the record noted no reason "
                "(removed after the record was written)")
    try:
        raw = zlib.decompress(row["body"])
    except zlib.error:
        return "the kept request is corrupted: it does not decompress"
    except TypeError:
        return "the kept request is corrupted: the body is not bytes"
    if hashlib.sha256(raw).hexdigest() != c.request_hash:
        return "the kept request is corrupted: it does not hash to request_hash"
    c.messages = json.loads(raw.decode("utf-8"))
    # W3 — the provider first, where the options name one this check never
    # re-sends (the stub; an API): a stub record has no seed and no digest
    # too, and those reasons are true but not why it cannot be re-run.
    options_problem, config = _options(conn, config_hash) if config_hash is not None else (None, None)
    provider = (config or {}).get("provider")
    if provider in NEVER_RESENT:
        c.config = config
        return NEVER_RESENT[provider]
    if c.seed is None:
        return f"no seed ({c.notes.get('inference_seed', 'no reason recorded')})"
    if config_hash is None:
        return f"no options ({c.notes.get('inference_config_hash', 'no reason recorded')})"
    if options_problem is not None:
        return options_problem
    c.config = config
    if c.config.get("model") != c.model_id:
        return f"the options name model {c.config.get('model')!r}, the record {c.model_id!r}"
    if c.digest is None:
        return f"no digest ({c.notes.get('model_digest', 'no reason recorded')})"
    if c.config.get("provider") not in REPLAYABLE_PROVIDERS:
        return (f"provider {c.config.get('provider')!r}: this check re-sends to Ollama only "
                f"(the one provider that names the weights it serves)")
    return None


def _options(conn, config_hash: str) -> tuple[str | None, dict | None]:
    """The options row the record keys, trusted only through that key (an
    edited row would otherwise be re-sent as though it were what the turn
    sent): (why it cannot be used, None) or (None, the options)."""
    cfg = conn.execute("SELECT config_json FROM inference_configs WHERE config_hash = ?",
                       (config_hash,)).fetchone()
    if cfg is None:
        return "the options row is missing", None
    if not isinstance(cfg["config_json"], str):
        return "the options row is not text", None
    if hashlib.sha256(cfg["config_json"].encode("utf-8")).hexdigest() != config_hash:
        return "the options row is corrupted: it does not hash to the record's key", None
    return None, json.loads(cfg["config_json"])


def captured_answers(path: Path | None) -> dict[str, str]:
    """lineage_id -> the answer text AO's citation capture kept, only where
    its own hash binds it (a mismatched row is ignored, never trusted)."""
    out: dict[str, str] = {}
    if path is None or not path.is_file():
        return out
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            text = row.get("answer")
            if isinstance(text, str) and row.get("lineage_id") and \
                    hashlib.sha256(text.encode("utf-8")).hexdigest() == row.get("answer_hash"):
                out[row["lineage_id"]] = text
    return out


# ---------------------------------------------------------------------------
# The daemon
# ---------------------------------------------------------------------------

class HttpDaemon:
    """Ollama over HTTP: `/api/tags` once, then the chat endpoint the record
    names, streamed (the record's options say `stream: true`)."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def tags(self) -> dict[str, str]:
        import httpx
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=httpx.Timeout(10.0, connect=5.0))
        except (httpx.HTTPError, OSError) as e:
            raise DaemonUnreachable(f"{self.base_url}/api/tags: {type(e).__name__}: {e}") from None
        if r.status_code != 200:
            raise DaemonUnreachable(f"{self.base_url}/api/tags answered {r.status_code}")
        out = {}
        for row in r.json().get("models", []):
            for key in ("name", "model"):
                if row.get(key) and row.get("digest"):
                    out[row[key]] = row["digest"]
        return out

    def chat(self, endpoint: str, body: dict) -> tuple[int, list[dict]]:
        import httpx
        chunks: list[dict] = []
        try:
            with httpx.stream("POST", f"{self.base_url}{endpoint}", json=body,
                              timeout=httpx.Timeout(None, connect=5.0)) as r:
                if r.status_code >= 400:
                    return r.status_code, []
                for line in r.iter_lines():
                    if line.strip():
                        try:
                            chunks.append(json.loads(line))
                        except ValueError:
                            continue
                return r.status_code, chunks
        except (httpx.HTTPError, OSError) as e:
            raise DaemonUnreachable(f"{self.base_url}{endpoint}: {type(e).__name__}: {e}") from None


def parse_reply(model: str, chunks: list[dict], *, separate: bool) -> tuple[str, str]:
    """(content stream, stated answer) from the daemon's chunks, by the
    production parse: `ollama_chunk_pieces` for the fields, `ReplySeparator`
    keyed on the model's tag pair for inline reasoning. The same functions
    `ReplyStream` runs, never a copy of them."""
    from backend.providers.base import ANSWER, ReplySeparator
    from backend.providers.model_map import think_tags
    from backend.providers.ollama import ollama_chunk_pieces
    sep = ReplySeparator(think_tags(model) if separate else None)
    raw, answer = [], []
    for chunk in chunks:
        for source, text in ollama_chunk_pieces(chunk, separate=separate):
            if source != "content" or not text:
                continue
            raw.append(text)
            answer += [c.text for c in sep.feed(text) if c.kind == ANSWER]
        if chunk.get("done"):
            break
    answer += [c.text for c in sep.flush() if c.kind == ANSWER]
    return "".join(raw), "".join(answer)


def first_difference(a: str, b: str) -> int | None:
    """Index of the first differing character, or None when equal."""
    if a == b:
        return None
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return min(len(a), len(b))


@dataclass
class Outcome:
    candidate: Candidate
    verdict: str
    detail: str
    offset: int | None = None


@dataclass
class Report:
    db: str
    mode: str
    outcomes: list[Outcome] = field(default_factory=list)
    aborted: str | None = None

    def counts(self) -> dict:
        reasons: dict[str, int] = {}
        for o in self.outcomes:
            if o.verdict == NOT_REPLAYABLE:
                key = o.detail.split(" (")[0].split(":")[0]
                reasons[key] = reasons.get(key, 0) + 1
        return {REPRODUCED: sum(o.verdict == REPRODUCED for o in self.outcomes),
                DIVERGED: sum(o.verdict == DIVERGED for o in self.outcomes),
                NOT_REPLAYABLE: sum(o.verdict == NOT_REPLAYABLE for o in self.outcomes),
                "would_send": sum(o.verdict == "would send" for o in self.outcomes),
                "not_replayable_by_reason": reasons}


def check(conn, *, last: int, daemon=None, answers: dict | None = None, db_label: str = "") -> Report:
    """Classify the last `last` records and, with a daemon, re-send each
    replayable one. Pure over its inputs: tests pass a fake daemon."""
    from backend.providers.ollama import ollama_name_key
    from backend.providers.replay_capture import wire_body
    report = Report(db=db_label, mode="confirm" if daemon is not None else "dry run")
    found = candidates(conn, last)
    answers = answers or {}
    served = None
    if daemon is not None and any(c.replayable for c in found):
        try:
            # Keyed by Ollama's own name equality, the rule the capture used.
            served = {ollama_name_key(name): digest for name, digest in daemon.tags().items()}
        except DaemonUnreachable as e:
            report.aborted = str(e)
    for c in found:
        if not c.replayable:
            report.outcomes.append(Outcome(c, NOT_REPLAYABLE, c.reason))
            continue
        if daemon is None:
            report.outcomes.append(Outcome(c, "would send", f"seed {c.seed}, digest {c.digest[:19]}…"))
            continue
        if served is None:
            report.outcomes.append(Outcome(c, NOT_REPLAYABLE, f"daemon unreachable ({report.aborted})"))
            continue
        now = served.get(ollama_name_key(c.model_id))
        if now is None:
            report.outcomes.append(Outcome(c, NOT_REPLAYABLE, f"tag not installed: {c.model_id}"))
            continue
        if now != c.digest:
            report.outcomes.append(Outcome(c, NOT_REPLAYABLE,
                                           f"the tag moved: recorded {c.digest}, served {now}"))
            continue
        body = wire_body(c.config, c.messages, c.seed)
        try:
            status, chunks = daemon.chat(c.config.get("endpoint", "/api/chat"), body)
        except DaemonUnreachable as e:
            report.outcomes.append(Outcome(c, NOT_REPLAYABLE, f"daemon unreachable ({e})"))
            continue
        if status >= 400:
            report.outcomes.append(Outcome(c, NOT_REPLAYABLE, f"the daemon refused the request ({status})"))
            continue
        separate = bool(c.config.get("nexus_reply_separation", True))
        raw, answer = parse_reply(c.model_id, chunks, separate=separate)
        same_raw = hashlib.sha256(raw.encode("utf-8")).hexdigest() == c.response_hash
        same_answer = (c.answer_hash is None
                       or hashlib.sha256(answer.encode("utf-8")).hexdigest() == c.answer_hash)
        if same_raw and same_answer:
            report.outcomes.append(Outcome(c, REPRODUCED, "response_hash and answer_hash equal"))
            continue
        original = answers.get(c.lineage_id)
        offset = first_difference(original, answer) if original is not None else None
        where = (f"first differing character at {offset} of the answer" if original is not None
                 else "offset unmeasurable: the record keeps hashes, and the answer was not captured")
        what = ", ".join(n for n, same in (("response_hash", same_raw), ("answer_hash", same_answer))
                         if not same)
        report.outcomes.append(Outcome(c, DIVERGED, f"{what} differ; {where}", offset))
    return report


def render(report: Report, *, provider_url: str | None) -> str:
    k = report.counts()
    lines = [f"# Replay check — {_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}", "",
             f"- database: `{report.db}` (read-only: mode=ro + write authorizer)",
             f"- mode: {report.mode}" + (f"; daemon {provider_url}" if provider_url else ""),
             f"- records examined: {len(report.outcomes)} (the last N, newest first)", ""]
    if report.aborted:
        lines += [f"**The daemon could not be reached:** {report.aborted}", ""]
    lines += ["| verdict | count |", "|---|---|",
              f"| reproduced | {k[REPRODUCED]} |", f"| diverged | {k[DIVERGED]} |",
              f"| not replayable | {k[NOT_REPLAYABLE]} |"]
    if report.mode == "dry run":
        lines.append(f"| would send (dry run) | {k['would_send']} |")
    lines += ["", "Not replayable, by reason (counted apart; never folded into a rate):", ""]
    lines += [f"- {reason}: {n}" for reason, n in sorted(k["not_replayable_by_reason"].items())] or ["- none"]
    lines += ["", "A divergence does not by itself mean the capture is insufficient. Ollama always sends "
              "`cache_prompt: true` to llama-server, and llama.cpp documents that this can make output "
              "nondeterministic across batch sizes. Run the check with nothing else using the daemon."]
    lines += ["", "| lineage_id | written (UTC) | model | verdict | detail |", "|---|---|---|---|---|"]
    for o in report.outcomes:
        when = _dt.datetime.fromtimestamp(o.candidate.ts, _dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        lines.append(f"| `{o.candidate.lineage_id}` | {when} | {o.candidate.model_id} | {o.verdict} | {o.detail} |")
    return "\n".join(lines) + "\n"


def nexus_serving() -> str | None:
    """`host:port` when something accepts a connection where NEXUS serves."""
    host = os.environ.get("NEXUS_HOST", "127.0.0.1").strip() or "127.0.0.1"
    host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    port = int(os.environ.get("NEXUS_PORT", "8000") or 8000)
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return f"{host}:{port}"
    except OSError:
        return None


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    load_env()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="the instance's kb.db; opened read-only")
    ap.add_argument("--last", type=int, default=20, help="how many of the newest records to examine")
    ap.add_argument("--confirm", action="store_true",
                    help="re-send each replayable record's kept request (without it: a dry run, nothing sent)")
    ap.add_argument("--report-dir", default="reports", help="where replay_check_<date>.md is written")
    ap.add_argument("--capture", default=None,
                    help="AO's citation capture (answer text, for offsets); default its configured path")
    args = ap.parse_args(argv)
    db = args.db
    print(f"database: {store_label(db)} (opened read-only: {read_only_how(db)})", flush=True)
    exists, why = store_exists(db)
    if not exists:
        print(f"REFUSED: {why}")
        return EXIT_ARGS
    if args.last < 1:
        print(f"REFUSED: --last {args.last}: at least one record must be examined")
        return EXIT_ARGS
    probe = open_read_only(db, url_ok=True)                   # W3b part 3: a database URL is taken too
    try:
        nexus = table_exists(probe, "lineage_records")
    except Exception as e:                        # noqa: BLE001 — either driver's error, named below
        nexus, why = False, f"{type(e).__name__}: {e}"
    else:
        why = "no lineage_records table"
    finally:
        probe.close()
    if not nexus:
        print(f"REFUSED: {db} is not a NEXUS store ({why})")
        return EXIT_ARGS
    serving = nexus_serving()
    if serving:
        print(f"REFUSED: something is listening on {serving}, where NEXUS serves. Stop NEXUS first: "
              f"requests it sends while this runs change the daemon's batching, and a replay beside "
              f"them would mean nothing.")
        return EXIT_SERVING
    from backend.eval.citation_capture import capture_path
    cap = Path(args.capture) if args.capture else capture_path()
    if not cap.is_absolute():
        cap = ROOT / cap
    provider_url = os.environ.get("OLLAMA_URL", "http://localhost:11434") if args.confirm else None
    conn = open_read_only(db, url_ok=True)
    report = check(conn, last=args.last, daemon=HttpDaemon(provider_url) if args.confirm else None,
                   answers=captured_answers(cap), db_label=store_label(db))
    conn.close()
    text = render(report, provider_url=provider_url)
    out_dir = Path(args.report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"replay_check_{_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%d')}.md"
    out.write_text(text, encoding="utf-8")
    k = report.counts()
    print(f"capture: {cap.as_posix()} ({'present' if cap.is_file() else 'absent: no answer text for offsets'})")
    print(f"mode: {report.mode}; examined {len(report.outcomes)}: reproduced {k[REPRODUCED]}, diverged "
          f"{k[DIVERGED]}, not replayable {k[NOT_REPLAYABLE]}" +
          (f", would send {k['would_send']}" if report.mode == "dry run" else ""))
    for reason, n in sorted(k["not_replayable_by_reason"].items()):
        print(f"   not replayable — {reason}: {n}")
    print(f"report: {out.as_posix()}")
    return EXIT_UNREACHABLE if report.aborted else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
