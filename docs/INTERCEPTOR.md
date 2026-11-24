# The interceptor

A recorder an agent cannot skip. You point an agent harness (Claude Code, or anything that speaks the Anthropic
Messages or the OpenAI-compatible Chat Completions protocol) at the interceptor as its model endpoint. Every model
exchange then passes through it on the way to the provider and is recorded: the request before it is sent, the
response when it is complete, both sealed into a hash chain that a stranger can verify offline with `talima-verify`.

It is a standalone Talima recorder. It runs with no memory engine: no knowledge store, no retrieval, no learning.
Of NEXUS it loads the record, the seal, the chain, the store and the lock, and four small helpers beside them (the
identity boundary, the `.env` reader, the data-root rule and the record's scope vocabulary): 26 modules, each
listed with its reason in `tests/test_w10c_standalone.py`. It adds nothing to a request's body and changes nothing
in a response.

It is for agent harnesses and the people who run them. `README.md` lists the other ways to use this project; the
console and the desktop bundle are in `docs/FIRST_RUN.md`, a server in `docs/DEPLOY.md`.

## What it records, and what it cannot see

For every exchange that passes through it:

| Recorded | How |
|---|---|
| The request, before it is sent | A row committed before anything is forwarded: where it went, the SHA-256 of the body's bytes, and each credential header it forwards, by name and fingerprint. The body's bytes are kept beside it (switch: `NEXUS_KEEP_REQUESTS`) |
| The response, when it is complete | A record: the SHA-256 of the request's bytes as the harness sent them, the SHA-256 of the response's bytes as the provider returned them, and the model the response names (`upstream-reported:<name>`: the provider's claim) |
| Each tool use the model asks for | An action of class `intercepted`: the tool's name, the id the model gave it, a hash of what it asked |
| Each tool result the harness sends back | Linked to that action as *reported*: a hash of the result, and whether the harness called it an error. A result is linked to the tool use the request itself repeats before it (the same id, name and input), never by its id alone. One ask is answered once, and several asks under one id are answered in the order they were made. A harness sends its whole history with every request, in whatever shape it keeps it: a result is a report when nothing of the assistant's follows it in the request, or when it answers the last time the request holds that ask; and a tool use that has its outcome is not written again |

What it cannot see: whether the harness ran a tool it was asked to run; anything the harness ran and did not
report; anything the harness does without calling the model. The class `intercepted` says what the recorder saw (the
model asking, the harness reporting). It never says a tool ran. A tool the provider runs itself (a server tool) asks
the harness nothing: it is in the response's bytes, which are hashed, and is not recorded as a tool use.

What a link does not say: which conversation a result came from. The recorder knows a tool use by what was
asked: the protocol, the id, the name and the input, with numbers compared as a harness whose numbers are floats
reads them (`30` and `30.0` are one number, and so are two integers too large for a float to tell apart; the
response's own hash is over its bytes, and does tell them apart). Two tool uses that agree in all of that are one
ask to the recorder, and some providers number their tool calls from the start in every conversation. For such
asks:

- a report goes to the tool use asked most recently that is still waiting, whichever conversation asked it, so a
  harness that repeats another conversation's ask word for word and then reports a result is linked to it;
- a request sent again while another such ask waits is taken for that ask's report: it cannot be told from a
  second conversation reporting the same;
- a request that is byte for byte the one a tool use was asked in reply to is taken for that request sent again,
  and reports nothing to that tool use;
- a report that the assistant's own words follow in the request is taken for history sent again when the store
  already holds as many tool uses with that ask and that result as the request holds answers of them;
- of the answers a request holds to one such ask, only the last can be a report once the assistant's words follow
  them: an earlier one the store never recorded stays unrecorded.

A provider that gives every tool use its own id never produces two such asks, and none of this arises.

**Coverage.** Complete for every exchange that passed through the endpoint; a harness pointed elsewhere is not
visible; tools are recorded as requested and as reported, not as executed.

**Credentials.** The key your harness sends in a header passes through to the provider and is never stored: the
record holds the header's name and the first 16 hex characters of the SHA-256 of its value. Its value is in no
file the interceptor writes and in nothing it prints. A header counts as a credential by its name
(`Authorization`, `Cookie`, `X-Api-Key`, `Api-Key`, `Apikey`, or a name ending in `-key`, `-token`, `-secret` or
`-auth`). A key a provider takes in the URL's query passes through too and is recorded nowhere: the record names
the URL's path, never its query.

**Content.** The request's body is kept as the harness sent it, compressed, so the store holds the conversation:
the system prompt, the messages, the tool results. `NEXUS_KEEP_REQUESTS=off` keeps the hashes and no body. A
response's bytes are hashed and never kept.

## Try it without a provider

The project ships a stub provider that speaks both protocols from a fixed script and checks no key. Three
terminals, from the repository's folder, with its Python environment active (`docs/FIRST_RUN.md`, steps 1 to 3):

```
python -m backend.intercept.stub_upstream --port 8791
```

```
export NEXUS_INTERCEPT_UPSTREAM=http://127.0.0.1:8791
export NEXUS_INTERCEPT_DATA_ROOT=$HOME/talima-intercept-data
python -m backend.intercept
```

```
curl -s http://127.0.0.1:8790/v1/messages -H 'content-type: application/json' -H 'x-api-key: not-a-key' \
  -d '{"model":"any","max_tokens":64,"messages":[{"role":"user","content":"Hello"}]}'
curl -s http://127.0.0.1:8790/health
```

On Windows PowerShell, set the two variables with `$env:NEXUS_INTERCEPT_UPSTREAM = "http://127.0.0.1:8791"` and
`$env:NEXUS_INTERCEPT_DATA_ROOT = "$HOME\talima-intercept-data"`. The released binary `talima-intercept` is the same
recorder as `python -m backend.intercept`, built without the memory engine: it refuses `NEXUS_INTERCEPT_INJECT=on`
by name.

`/health` answers with counters: `exchanges`, `recorded`, `refused_before_sending`, `incomplete`,
`upstream_unreachable`, `sealed_blocks` and `unsealed`. No content and no identity.

## Point a harness at it

Set the provider the interceptor forwards to, then give the harness the interceptor's address as its base URL.

**Claude Code.** Start the interceptor with `NEXUS_INTERCEPT_UPSTREAM=https://api.anthropic.com`, then start Claude
Code with `ANTHROPIC_BASE_URL=http://127.0.0.1:8790`. Anthropic's gateway pages (code.claude.com/docs/en/llm-gateway
and /llm-gateway-protocol) say what a gateway must do, and the interceptor does it: inference arrives on
`/v1/messages`; the `anthropic-version` and `anthropic-beta` headers and the credential headers are forwarded as
sent; a stream is relayed event by event, pings included, through its last event. Two optional endpoints are not
served, `/v1/messages/count_tokens` and `/v1/models`: Claude Code then estimates token counts itself and uses its
built-in model list. They are answered 404, forwarded nowhere and recorded nowhere.

Which sign-in works, from the same pages: with an API key or a gateway credential configured, requests carry that
credential, and the interceptor forwards it. With only the base URL set, Claude Code keeps using its saved claude.ai
login, and a gateway that passes that on must forward `Authorization` and the `anthropic-beta` header unchanged,
which a pass-through does. The project's own tests call no provider, so neither sign-in is tested against Anthropic
here: `scripts/intercept_smoke.py` is the check you run yourself, with your own key.

**An OpenAI-compatible harness.** Start the interceptor with the provider's address as its upstream (a hosted
service, or a local server such as `http://127.0.0.1:11434`), and set the harness's base URL (the OpenAI SDKs read
`OPENAI_BASE_URL`) to `http://127.0.0.1:8790/v1`. Inference arrives on `/v1/chat/completions`.

One interceptor forwards to one upstream. For two providers run two, each with its own data folder and port
(`--port`).

## Verify a session

Stop the interceptor (Ctrl+C): it seals what is unsealed, then releases its store. One process writes a store at a
time, so the commands below run while it is stopped; started while it runs, they exit 7 and name the holder. They
need the data folder's setting and no upstream. They are not read-only: each first marks `incomplete` any exchange
a stopped process left unfinished, and all but `list` then seal what is unsealed. Given a folder that holds no
store, each makes an empty one there and reports on that, so a command that shows nothing may be reading the wrong
folder.

```
python -m backend.intercept verify
python -m backend.intercept list
python -m backend.intercept identity --out identity.json
python -m backend.intercept credential <lineage id> --disclose record_scope,model_id,request_hash,response_hash --out credential.json
python -m backend.intercept receipt <action id> --out receipt.json
python -m backend.intercept chain --out chain.json
```

- `verify` re-checks the whole store: the chain's hashes and links; every record against its seal; every action
  whose outcome is written against its seal, both ways (the row against the block that sealed it, and every leaf
  a block sealed against the row the store holds now, so an outcome replaced or erased after its seal is named);
  who each action is attributed to, against the commitment in its row; and every tool use against the sealed
  record that names it (a tool use has no outcome, so no seal of its own, until a harness reports its result):
  its row as it was asked and, once it has an outcome, the hash of its own row. It prints the result as JSON and
  exits 0 only when everything verifies.
- `list` prints one line of JSON per exchange, newest first: its ids, state, hashes, and its tool uses.
- `identity` writes the instance's public keys. A verifier pins signatures to them.
- `credential` writes one record's credential, and `receipt` one action's. A field's value is in a credential only
  when you name it in `--disclose`; every other field is there as a salted commitment.
- `chain` writes the whole chain as one document: every block's header, the hashes each block sealed and its
  signature. It holds hashes and no content.

Anyone can then check them, on any machine, with nothing of this project but the verifier
(`scripts/talima_verify.py`, or the released `talima-verify`):

```
talima-verify credential.json --identity identity.json
talima-verify --action receipt.json --identity identity.json
talima-verify --chain chain.json --identity identity.json
```

What each of these files is, and every rule the verifier holds them to, is in `docs/TALIMA_SPEC.md`.

To check that a record is of the bytes you hold, hash them: `request_hash` is the SHA-256 of the request body
exactly as the harness sent it, and `response_hash` of the response body exactly as the provider returned it
(compressed, if the provider compressed it).

## When something goes wrong

The interceptor fails closed: a harness holds a completed response only if its record is written.

| What happened | What the harness gets | What the store holds |
|---|---|---|
| The request's record cannot be written | HTTP 503, `talima_request_not_recorded`. The provider is never called | Nothing |
| The provider cannot be reached | HTTP 502, `talima_upstream_unreachable` | The request's row, `failed`, with the error's class |
| The response's record cannot be written, streamed | Every event before the last, then an error event `talima_response_not_recorded` in the last one's place | The request's row, `incomplete` (if that write fails too, at the next write the store takes, or at the next start). No record |
| The same, for a response that is not a stream | HTTP 503, the response withheld | The same |
| The same, for a compressed stream | The connection is cut before the stream's end | The same |
| The same, for a response in an encoding the interceptor does not read | HTTP 503: none of it was relayed | The same |
| The provider's stream breaks off | The connection is cut, as it would be without the interceptor | The request's row, `incomplete`, and a record of the bytes that arrived, which says it was cut |
| The harness goes away mid-response | Nothing | The same |
| The interceptor stops mid-exchange | A dropped connection | The request's row, marked `incomplete` at the next start |

Bytes already relayed when a write failed are not recalled. The row says which case it was. When nothing of a
response has reached the harness yet, it gets the error whole in place of a cut connection or an error event:
HTTP 503 `talima_response_not_recorded` when the record could not be written, HTTP 502
`talima_upstream_stream_broke` when the provider's response broke off.

A request that is not for a model endpoint, a request a browser sent from another site, and a request addressed to
a name that is not this machine's are refused (404 or 403), forwarded nowhere and recorded nowhere.

## Settings

| Setting | What it is |
|---|---|
| `NEXUS_INTERCEPT_UPSTREAM` | The provider to forward to: scheme, host, optional port and path prefix. Required to run the interceptor (the commands that read a stopped store need none). Plain `http` is accepted only for this machine |
| `NEXUS_INTERCEPT_DATA_ROOT` | The folder the interceptor's own store lives in. Required. Never the console's: a folder that holds a store the interceptor did not make is refused |
| `NEXUS_INTERCEPT_OWNER` | The accountable identity on every record. Asserted, never authenticated. Unset: unattributed |
| `NEXUS_INTERCEPT_INJECT` | `off` (the default) or `on`. See below |
| `NEXUS_INTERCEPT_PROXY_HEADERS` | Behind an identity proxy: the proxy's own headers, named, in place of the guess from the identity header's name. See below |
| `--host`, `--port` | Where to listen: an address, never a name, behind a proxy too (`127.0.0.1`, `::1`). Default `127.0.0.1:8790` |

A setting the interceptor refuses is named on stderr and the process exits 2 having opened nothing.

**Loopback only.** The interceptor listens on this machine. It refuses any other address unless it runs behind an
identity proxy (`NEXUS_AUTH_MODE=proxy`, `docs/DEPLOY.md`): then it answers only requests the proxy has attached
the owner's login to, the record names that login with `identity_mechanism: proxy`, and the proxy's own headers
are not forwarded to the provider. Always kept back: the identity header, `Forwarded`, every `X-Forwarded-...`
header, `X-Real-Ip` and `Via`.

The proxy's other headers are known one of two ways. By default they are guessed from the identity header's name:
every header that begins with its first word (for `Tailscale-User-Login`, every `Tailscale-...` header; for
`X-Auth-Request-Email`, every `X-Auth-...` header), and none where the name is too short to have such a word
(`X-User`). The guess can be wrong both ways. It keeps back a header of your harness's that shares the word:
behind a proxy whose identity header is `X-Goog-Authenticated-User-Email`, a provider's `X-Goog-Api-Key` does not
arrive. And it does not see a proxy header that does not share it. So you can name them instead:
`NEXUS_INTERCEPT_PROXY_HEADERS` is a list of header names separated by commas, each exact or a prefix ending in
`*` (`X-Goog-Iap-*, X-Email`). When it names a header, no guess is made; an empty value is the same as unset.
To keep back nothing but the identity header and the forwarding headers, name the identity header itself.

**Injection, built and off.** With `NEXUS_INTERCEPT_INJECT=on` the interceptor stops being a pure recorder: it
loads NEXUS's memory engine, searches the knowledge store in its own data folder with each request's last user
message, and, when it finds something, adds one delimited system block (`<nexus-block source="retrieval">`) to the
request. The record then holds both hashes: `request_hash` of what the harness sent, and, on the request's row, of
what was forwarded. Off, which is the default, nothing is added and the engine is never loaded. Injection runs
from a checkout: the released binary holds no engine, and refuses `on` by name before it opens anything.

## What it does to a request, exactly

- The body is forwarded byte for byte.
- Headers are forwarded with their values and order as sent, byte for byte. Their names arrive in lower case
  (HTTP's header names have no case). One value is narrowed: `Accept-Encoding` asks the provider only for
  encodings the interceptor reads (gzip, deflate, identity): of those, the ones your harness named and did not
  refuse, in its order, and `identity` (no compression) when that leaves none, even where your harness refused
  it. A response the interceptor cannot read is one whose model and tool uses it cannot record. Headers that belong to one
  connection are not forwarded (`Connection` and the headers it names, `Keep-Alive`, `Transfer-Encoding`, `TE`,
  `Trailer`, `Upgrade`, `Proxy-Authenticate`, `Proxy-Authorization`, `Proxy-Connection`); `Host` and
  `Content-Length` are set for the provider's connection.
- The response's status, headers and body are relayed as they came, less the same connection headers and, for a
  stream, a `Content-Length` (the server frames a stream itself).
- A stream is relayed event by event. Its last event (`message_stop` or `error`; `data: [DONE]`) is held until
  the record is written, which takes a few milliseconds. A stream that closes without that event has nothing to
  hold: every event has reached the harness when it closes, and if the record then cannot be written the error
  event follows them. A compressed stream is relayed as compressed. A response in an encoding the interceptor
  cannot read (anything but gzip and deflate; a provider should send none, since none was asked for) is held
  whole until its record is written and then relayed as it came; its model and tool uses are not read, and the
  record says so.
- The provider has ten minutes between two pieces of a response before the interceptor gives up on it.
