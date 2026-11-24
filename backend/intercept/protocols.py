"""W10c (run/specs/W10c.md S4, S6) — the two wire protocols the interceptor relays, read and never rewritten.

Anthropic Messages (`POST /v1/messages`) and OpenAI-compatible Chat Completions (`POST /v1/chat/completions`). This
module says, for each: which path it is; which event ends a stream; what a response says about the model that
answered and the tool uses it asks for; what a request says about tool results; and how an error of the
interceptor's own is spelled so the harness reads it as an error. Everything here is a pure function of bytes.

Nothing here can cost an exchange its record. A body that is not what the protocol says (not JSON, not UTF-8, a
field of another type) reads as "nothing found" with the reason in `note`; the bytes are relayed and hashed
whatever they hold. A harness's and a provider's words are data: no value read here is evaluated, formatted into
a statement or used as a path.
"""
from __future__ import annotations

import hashlib
import json
import re
import zlib
from dataclasses import dataclass, field

ANTHROPIC = "anthropic-messages"
OPENAI = "openai-chat"
ROUTES = {"/v1/messages": ANTHROPIC, "/v1/chat/completions": OPENAI}
UNKNOWN_MODEL = "unknown"
# How much of a provider's or a harness's own naming a row keeps: an id or a name longer than this is cut and the
# cut is declared in the row's text (`...`), never silently.
NAME_LIMIT = 200


def protocol_of(path: str) -> "str | None":
    return ROUTES.get(path)


def canonical(value) -> bytes:
    """The one serialisation hashed here: sorted keys, no white space, ASCII. A number JSON cannot carry (an
    infinity, a NaN) is written as Python writes it, the keys still sorted, so a value that holds one hashes
    rather than raises, whatever order its members came in."""
    for allow_nan in (False, True):
        try:
            return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                              allow_nan=allow_nan).encode("ascii")
        except (TypeError, ValueError):
            continue
    return json.dumps(repr(value), ensure_ascii=True).encode("ascii")


def _clip(text) -> str:
    """A provider's or a harness's own naming, as a row keeps it: text a store can hold (half of a character pair,
    which JSON can spell and a file cannot hold, is spelled out), cut at NAME_LIMIT with the cut declared."""
    text = text if isinstance(text, str) else repr(text)
    text = text.encode("utf-8", "backslashreplace").decode("utf-8")
    return text if len(text) <= NAME_LIMIT else text[:NAME_LIMIT] + "..."


# ---------------------------------------------------------------- what the interceptor says in its own voice

def error_body(protocol: str, reason: str, why: str) -> bytes:
    """A whole response body that says `reason: why`, as the protocol spells an error."""
    message = f"{reason}: {why}"
    if protocol == OPENAI:
        return canonical({"error": {"message": message, "type": "api_error", "param": None, "code": reason}})
    return canonical({"type": "error", "error": {"type": "api_error", "message": message}})


def error_event(protocol: str, reason: str, why: str) -> bytes:
    """One server-sent event that says `reason: why`, to end a stream that cannot end as the upstream's did."""
    if protocol == OPENAI:
        return b"data: " + error_body(protocol, reason, why) + b"\n\n"
    return b"event: error\ndata: " + error_body(protocol, reason, why) + b"\n\n"


# ---------------------------------------------------------------- server-sent events

# A blank line: two line terminators in a row, each CRLF, LF or CR. Atomic, so a CRLF is one terminator, never two.
_BLANK_LINE = re.compile(rb"(?>\r\n|\n|\r)(?>\r\n|\n|\r)")


def split_events(data: bytes) -> "tuple[list, bytes]":
    """(the complete events of `data`, each with its terminator; the bytes after the last complete one). An event
    ends at a blank line, whichever line endings the stream uses."""
    events, start = [], 0
    for m in _BLANK_LINE.finditer(data):
        events.append(data[start:m.end()])
        start = m.end()
    return events, data[start:]


def _fields(event: bytes) -> "tuple[str, str]":
    """An event's (name, data), per the server-sent events format; a comment line (`:`) is neither."""
    name, datas = "", []
    for line in event.replace(b"\r\n", b"\n").replace(b"\r", b"\n").split(b"\n"):
        if not line or line.startswith(b":"):
            continue
        key, _, value = line.partition(b":")
        value = value[1:] if value.startswith(b" ") else value
        if key == b"event":
            name = value.decode("utf-8", "replace")
        elif key == b"data":
            datas.append(value.decode("utf-8", "replace"))
    return name, "\n".join(datas)


def is_terminal(protocol: str, event: bytes) -> bool:
    """The event after which the upstream's stream is over: Anthropic's `message_stop` (and its `error`), OpenAI's
    `[DONE]`."""
    name, data = _fields(event)
    if protocol == OPENAI:
        return data.strip() == "[DONE]"
    if name.strip() in ("message_stop", "error"):
        return True
    if data:                                    # whatever the event is called, its data's own type says what it is
        try:
            return json.loads(data).get("type") in ("message_stop", "error")
        except (ValueError, AttributeError):
            return False
    return False


class Scanner:
    """Where, in a stream of server-sent events fed as it arrives, the terminal event begins.

    `safe` is how many of the bytes fed so far may be relayed: every complete event before the terminal one. The
    bytes of an event still arriving are not safe (it may turn out to be the terminal one), and nothing from the
    terminal event on is, until the exchange's record is written. `finish()` reads an event the stream ended
    inside (a last line with no blank line after it), which is terminal as much as a complete one."""

    def __init__(self, protocol: str):
        self.protocol = protocol
        self.safe = 0                 # offset: everything before it may be relayed
        self.terminal_at = None       # offset of the terminal event's first byte, once seen
        self._tail = b""              # the bytes after the last complete event
        self._searched = 0            # how much of the tail holds no blank line (never searched twice)

    @property
    def at_boundary(self) -> bool:
        return not self._tail

    def feed(self, data: bytes) -> None:
        if self.terminal_at is not None or not data:
            return
        buffer = self._tail + data
        start = 0
        for m in _BLANK_LINE.finditer(buffer, max(0, self._searched - 3)):
            if m.end() <= start:
                continue
            if is_terminal(self.protocol, buffer[start:m.end()]):
                self.terminal_at = self.safe
                self._tail = buffer[start:]
                return
            self.safe += m.end() - start
            start = m.end()
        self._tail = buffer[start:]
        self._searched = len(self._tail)

    def finish(self) -> None:
        if self.terminal_at is None and self._tail and is_terminal(self.protocol, self._tail):
            self.terminal_at = self.safe


# ---------------------------------------------------------------- a response's content encoding

# The content encodings this module reads. A response in any other cannot be read for its last event, its model
# or its tool uses, so a request asks for none (`accept_encoding`), and one that arrives anyway is held whole.
READABLE_ENCODINGS = ("gzip", "x-gzip", "deflate", "identity")


def _refused(part: str) -> bool:
    """Whether a coding's weight is zero (`gzip;q=0`): the harness named it to refuse it."""
    for parameter in part.split(";")[1:]:
        name, _, weight = parameter.partition("=")
        if name.strip().lower() == "q":
            try:
                return float(weight.strip()) == 0.0
            except ValueError:
                return False
    return False


def accept_encoding(value: str) -> str:
    """A harness's `Accept-Encoding` narrowed to the encodings read here: the codings it named that are readable
    and that it did not refuse (a weight of zero), in its order and with its weights; `identity` when none is
    left. `*` is not kept: it stands for every coding not named, and those are the ones not read here."""
    kept = [part.strip() for part in value.split(",")
            if part.split(";", 1)[0].strip().lower() in READABLE_ENCODINGS and not _refused(part)]
    return ", ".join(kept) if kept else "identity"


GZIP_MAGIC = b"\x1f\x8b"


class Inflater:
    """An incremental decoder for gzip and deflate, member after member: a gzip body may be several members one
    after another, and a decoder that stopped at the first would leave the rest unread. What follows the last
    member and is not another (it does not open with gzip's two magic bytes, or opens with them and does not
    decode) is not part of the body: what the members held is read, the rest is passed over, and `passed_over`
    counts its bytes. A body whose FIRST member does not decode is not read at all.

    A later member that fails part-way has, fed in pieces, already given what it held before it failed; fed
    whole, it gives nothing. The record is read from the whole body in one feed (`decoded`), so what a record
    says does not depend on how its stream arrived; the pieces are read only to find the stream's last event."""

    def __init__(self):
        self._member = zlib.decompressobj(47)    # gzip or zlib, by its own header
        self._first = True                       # the member being read is the body's first
        self._fed = 0                            # bytes given to a later member so far
        self._after = b""                        # bytes after a member, too few yet to say what they begin
        self._over = False                       # the body is over: what follows is not a member
        self.passed_over = 0                     # bytes after the last readable member, not read

    def feed(self, data: bytes) -> bytes:
        out, data = b"", self._after + data
        self._after = b""
        while data and not self._over:
            if self._member.eof:
                if len(data) < len(GZIP_MAGIC):
                    self._after = data
                    return out
                if not data.startswith(GZIP_MAGIC):
                    self._over = True
                    break
                self._member, self._first, self._fed = zlib.decompressobj(47), False, 0
            try:
                out += self._member.decompress(data)
            except zlib.error:
                if self._first:
                    raise
                self._over, self.passed_over = True, self.passed_over + self._fed   # it opened like a member
                break
            rest = self._member.unused_data if self._member.eof else b""
            self._fed += len(data) - len(rest)
            data = rest
        if self._over:
            self.passed_over += len(data)
        return out

    def unread(self) -> int:
        """Once the body has ended: the bytes after its last readable member, none of them read. What was passed
        over, a last byte held back to see what it began, and a later member that began and never ended."""
        unfinished = self._fed if not (self._first or self._member.eof or self._over) else 0
        return self.passed_over + len(self._after) + unfinished


def decoder_for(content_encoding: str):
    """An incremental decoder for the encoding, `None` for none needed, or `False` when it cannot be read here."""
    enc = (content_encoding or "").strip().lower()
    if enc in ("", "identity"):
        return None
    if enc in READABLE_ENCODINGS:
        return Inflater()
    return False


def decoded(raw: bytes, content_encoding: str) -> "tuple[bytes | None, str | None]":
    """(the body decoded, None) or (None, why it could not be)."""
    dec = decoder_for(content_encoding)
    if dec is None:
        return raw, None
    if dec is False:
        return None, f"content-encoding {_clip(content_encoding)!r} is not read here"
    try:
        body = dec.feed(raw)
    except zlib.error as e:
        return None, f"the body did not decode as {_clip(content_encoding)!r} ({type(e).__name__})"
    unread = dec.unread()
    return body, (f"{unread} byte(s) after the body's last readable member were not read" if unread else None)


# ---------------------------------------------------------------- what a request and a response say

@dataclass(frozen=True)
class ToolUse:
    """A tool use the model asks for: its id and name as the model gave them, and the hash of what it asked."""
    tool_use_id: str
    name: str
    request_hash: str


@dataclass(frozen=True)
class ToolResult:
    """A tool result the harness reports: the id it answers, whether the harness calls it an error, its hash;
    and which tool use it answers, as the request's own history says: the hash of the ask the request repeats
    before it under that id (None when it repeats none, or when every ask under the id already has its result
    in the request). `latest`: it answers the last ask of that hash the request holds. `current`: no message of
    the assistant's follows it in the request. A result is linked by these (`Recorder._reported`), never by its
    id alone."""
    tool_use_id: str
    is_error: bool
    response_hash: str
    ask_hash: "str | None" = None
    current: bool = False
    latest: bool = False


@dataclass(frozen=True)
class RequestFacts:
    results: tuple = ()
    note: "str | None" = None


@dataclass(frozen=True)
class ResponseFacts:
    model: "str | None" = None
    uses: tuple = ()
    note: "str | None" = None
    counts: dict = field(default_factory=dict)


EXACT_INTEGERS = 2 ** 53                    # the integers a binary64 float holds exactly: what a JSON number is,
                                            # to a harness written in a language whose numbers are floats


def _number(value):
    """One number as a harness whose numbers are floats reads it: `30.0` and `30` are one number, `1e2` and `100`
    are one number, an integer past what a float holds exactly is the float nearest it, and one past every float
    is the infinity such a reader makes of it. Anything that is not a number is itself."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if isinstance(value, int):
        if abs(value) <= EXACT_INTEGERS:
            return value
        try:
            value = float(value)
        except OverflowError:
            return float("inf") if value > 0 else float("-inf")
    return int(value) if value == value and abs(value) <= EXACT_INTEGERS and value.is_integer() else value


def _by_value(value):
    """A JSON value with each number as its value, not its spelling (`_number`). A harness parses the model's ask
    and writes it again in its own language's spelling, so the ask is compared this way. Two asks that differ
    only in an integer past 2**53 are therefore one ask here, as they are to such a harness; the response's own
    hash is over the bytes and tells them apart. Walked without recursion, so it adds no limit of its own: the
    JSON reader and writer keep theirs (about 990 levels at the interpreter's default), and a response nested
    past it is recorded as not read."""
    if not isinstance(value, (list, dict)):
        return _number(value)
    top = [None]
    todo = [(top, 0, value)]
    while todo:
        into, key, item = todo.pop()
        if isinstance(item, list):
            into[key] = made = [None] * len(item)
            todo.extend((made, i, v) for i, v in enumerate(item))
        elif isinstance(item, dict):
            into[key] = made = {}
            todo.extend((made, k, v) for k, v in item.items())
        else:
            into[key] = _number(item)
    return top[0]


def asked_of(protocol: str, value):
    """What a tool use asked, in the one form that is hashed: its numbers by value (`_by_value`). Anthropic's
    `input` is a JSON value. An OpenAI-compatible `arguments` is a string: the JSON value it spells when it spells
    one (a harness that sends it back may respell it), else the string as the model wrote it. Nothing asked is
    nothing asked however it is spelled: absent, null, an empty string, the string `null`, `{}`."""
    if protocol == OPENAI and isinstance(value, str):
        if not value.strip():
            return {}
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return value
    return {} if value is None else _by_value(value)


def tool_use_hash(protocol: str, tool_use_id: str, name: str, asked) -> str:
    """What the model asked, as one hash: the protocol, the id and name it gave, and what it asked (`asked_of`).
    The whole id is in it, however long: a row keeps only the id's first characters to show."""
    return hashlib.sha256(canonical({"protocol": protocol, "id": tool_use_id, "name": name,
                                     "asked": asked_of(protocol, asked)})).hexdigest()


def result_hash(content) -> str:
    return hashlib.sha256(canonical(content)).hexdigest()


def read_request(protocol: str, body: bytes) -> RequestFacts:
    """The tool results a request reports, in the order it gives them, each with the ask the request's own
    history puts before it: a harness sends the model's tool use back (an assistant message) and then its result.
    One ask is answered once. A provider may give several asks one id (the function's name), so the asks under
    an id are answered in the order they were made: a result is the oldest unanswered ask's, and a result under
    an id with no ask left unanswered answers nothing. An ask the harness never answered is given up when its id
    is asked again after the harness has spoken. The shape of the history is the harness's (one assistant message
    per tool use, text between, rounds unrolled), so nothing here depends on it but two facts each result
    carries: whether it answers the LAST ask of its kind in the request, and whether any assistant message
    follows it."""
    try:
        doc = json.loads(body.decode("utf-8"))
        messages = doc.get("messages") if isinstance(doc, dict) else None
        if not isinstance(messages, list):
            return RequestFacts(note="the request holds no list of messages")
        found, waiting, times, last_assistant, spoken = [], {}, {}, -1, 0
        # waiting: id -> the asks not yet answered under it, oldest first: (the ask's hash, which time the request
        # holds that hash, how many messages of the harness's came before it)

        def asked(tool_use_id, name, value) -> None:
            if isinstance(tool_use_id, str) and isinstance(name, str):
                ask = tool_use_hash(protocol, tool_use_id, name, value)
                times[ask] = times.get(ask, 0) + 1
                kept = [a for a in waiting.get(tool_use_id, ()) if a[2] == spoken]     # the others are given up
                waiting[tool_use_id] = kept + [(ask, times[ask], spoken)]

        def answered(at: int, tool_use_id: str, is_error: bool, content) -> None:
            asks = waiting.get(tool_use_id)
            ask, nth, _when = asks.pop(0) if asks else (None, 0, 0)
            found.append((tool_use_id, is_error, result_hash(content), ask, nth, at))

        for at, message in enumerate(messages):
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            if role == "assistant":
                last_assistant = at
            else:
                spoken += 1
            if protocol == OPENAI:
                if role == "assistant" and isinstance(message.get("tool_calls"), list):
                    for call in message["tool_calls"]:
                        fn = call.get("function") if isinstance(call, dict) else None
                        if isinstance(fn, dict):
                            asked(call.get("id"), fn.get("name"), fn.get("arguments"))
                elif role == "tool" and isinstance(message.get("tool_call_id"), str):
                    answered(at, message["tool_call_id"], False, message.get("content"))
                continue
            content = message.get("content")
            for block in content if isinstance(content, list) else []:
                if not isinstance(block, dict):
                    continue
                if role == "assistant" and block.get("type") == "tool_use":
                    asked(block.get("id"), block.get("name"), block.get("input"))
                elif role == "user" and block.get("type") == "tool_result" \
                        and isinstance(block.get("tool_use_id"), str):
                    answered(at, block["tool_use_id"], block.get("is_error") is True, block.get("content"))
        return RequestFacts(results=tuple(
            ToolResult(i, e, h, ask, current=ask is not None and at > last_assistant,
                       latest=ask is not None and nth == times[ask]) for i, e, h, ask, nth, at in found))
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        return RequestFacts(note=f"the request was not read ({type(e).__name__})")


def _json_events(body: bytes):
    events, _tail = split_events(body)
    for event in events:
        name, data = _fields(event)
        if not data or data.strip() == "[DONE]":
            continue
        try:
            doc = json.loads(data)
        except ValueError:
            continue
        if isinstance(doc, dict):
            yield name, doc


def _anthropic_stream(body: bytes) -> ResponseFacts:
    model, blocks, order = None, {}, []
    for _name, doc in _json_events(body):
        kind = doc.get("type")
        if kind == "message_start" and isinstance(doc.get("message"), dict):
            model = doc["message"].get("model", model)
        elif kind == "content_block_start" and isinstance(doc.get("content_block"), dict) \
                and doc["content_block"].get("type") == "tool_use":
            block = doc["content_block"]
            blocks[doc.get("index")] = {"id": block.get("id"), "name": block.get("name"), "parts": [],
                                        "input": block.get("input")}
            order.append(doc.get("index"))
        elif kind == "content_block_delta" and isinstance(doc.get("delta"), dict) \
                and doc["delta"].get("type") == "input_json_delta" and doc.get("index") in blocks:
            part = doc["delta"].get("partial_json")
            if isinstance(part, str):
                blocks[doc.get("index")]["parts"].append(part)
    uses = []
    for index in order:
        b = blocks[index]
        text = "".join(b["parts"])
        try:
            asked = json.loads(text) if text else (b["input"] if b["input"] is not None else {})
        except ValueError:
            asked = text                            # not JSON: what the model wrote, as it wrote it
        uses.append((b["id"], b["name"], asked))
    return _facts(ANTHROPIC, model, uses)


def _anthropic_whole(body: bytes) -> ResponseFacts:
    doc = json.loads(body.decode("utf-8"))
    if not isinstance(doc, dict):
        return ResponseFacts(note="the response is not a JSON object")
    content = doc.get("content") if isinstance(doc.get("content"), list) else []
    uses = [(b.get("id"), b.get("name"), b.get("input")) for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"]
    return _facts(ANTHROPIC, doc.get("model"), uses)


def _openai_stream(body: bytes) -> ResponseFacts:
    model, calls, order = None, {}, []
    for _name, doc in _json_events(body):
        model = doc.get("model", model) if isinstance(doc.get("model"), str) else model
        for choice in doc.get("choices") if isinstance(doc.get("choices"), list) else []:
            delta = choice.get("delta") if isinstance(choice, dict) else None
            for call in (delta.get("tool_calls") if isinstance(delta, dict) and
                         isinstance(delta.get("tool_calls"), list) else []):
                if not isinstance(call, dict):
                    continue
                key = (choice.get("index"), call.get("index"))
                if key not in calls:
                    calls[key] = {"id": None, "name": None, "parts": []}
                    order.append(key)
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                calls[key]["id"] = call.get("id") or calls[key]["id"]
                calls[key]["name"] = fn.get("name") or calls[key]["name"]
                if isinstance(fn.get("arguments"), str):
                    calls[key]["parts"].append(fn["arguments"])
    return _facts(OPENAI, model, [(calls[k]["id"], calls[k]["name"], "".join(calls[k]["parts"])) for k in order])


def _openai_whole(body: bytes) -> ResponseFacts:
    doc = json.loads(body.decode("utf-8"))
    if not isinstance(doc, dict):
        return ResponseFacts(note="the response is not a JSON object")
    uses = []
    for choice in doc.get("choices") if isinstance(doc.get("choices"), list) else []:
        message = choice.get("message") if isinstance(choice, dict) else None
        for call in (message.get("tool_calls") if isinstance(message, dict) and
                     isinstance(message.get("tool_calls"), list) else []):
            fn = call.get("function") if isinstance(call, dict) and isinstance(call.get("function"), dict) else {}
            if isinstance(call, dict):
                uses.append((call.get("id"), fn.get("name"), fn.get("arguments")))
    return _facts(OPENAI, doc.get("model"), uses)


def _facts(protocol: str, model, uses: list) -> ResponseFacts:
    kept, unnamed = [], 0
    for tool_use_id, name, asked in uses:
        if not isinstance(tool_use_id, str) or not tool_use_id or not isinstance(name, str) or not name:
            unnamed += 1                         # a tool use with no id or no name cannot be linked to its result
            continue
        kept.append(ToolUse(tool_use_id, name, tool_use_hash(protocol, tool_use_id, name, asked)))
    return ResponseFacts(model=_clip(model) if isinstance(model, str) and model else None, uses=tuple(kept),
                         note=(f"{unnamed} tool use(s) with no id or no name were not recorded as actions"
                               if unnamed else None),
                         counts={"tool_uses": len(kept), "unnamed_tool_uses": unnamed})


def read_response(protocol: str, body: "bytes | None", *, streamed: bool) -> ResponseFacts:
    """The model the response names and the tool uses it asks for. `body` is the decoded body, or None when it
    could not be decoded."""
    if body is None:
        return ResponseFacts(note="the response was not read: its body could not be decoded")
    try:
        if protocol == OPENAI:
            return _openai_stream(body) if streamed else _openai_whole(body)
        return _anthropic_stream(body) if streamed else _anthropic_whole(body)
    except (ValueError, UnicodeDecodeError, RecursionError, AttributeError, TypeError) as e:
        return ResponseFacts(note=f"the response was not read ({type(e).__name__})")
