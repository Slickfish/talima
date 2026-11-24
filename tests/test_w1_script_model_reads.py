"""W1 part 2 — scripts take a model's text through the reply chokepoint.

C-38 made `backend/providers/base.py`'s `reply_stream` the one place a
model's reply is separated into its answer and its reasoning, and its guards
cover the tracked backend. No guard covered how a SCRIPT obtains generated
text. W1's establish found three sites that took it straight from
`stream_chat`. `citation_judge.py`: a draft judgement inside an inline
`<think>` span was parsed as the judge's verdict (demonstrated).
`measure_vocabulary_compliance.build_corpus`: a global `</think>` split that
kept an unclosed span as the answer. The extraction arm (`run_arm`) mirrors
production extraction's own direct call and stays (Ruling 2).

R1  THE GUARD, derived from what a script REACHES, not from a list of
    method names (W1's verifier, lens 3 F1: 19 of 25 ordinary forms of a
    POST to a model evaded the first version). Over every tracked script and
    every tracked root .py (N1): each import of a network, socket or model
    client module or of `backend.providers`; each read of `stream_chat` or
    `stream_pieces`, called or not, and each `getattr` naming one; in a
    script that imports such a module, each call of a sending name; each
    call of a method on a provider; each process call naming curl or ollama.
    Every site is counted (F5: a second call under an answered key used to
    pass) and answered for below with its reason, frozen both ways.
R1c Companions, each planting a form the first version let through and
    asserting R1's own check fails: a urllib opener POST, a `from httpx
    import post` alias, a client `send` of a built request, a bound alias
    and a `getattr` of `stream_chat`, a new provider method, curl through
    subprocess, a second send under an answered key, and an answered site
    that is gone.
R2  THE BEHAVIOUR: the judge ignores a draft judgement inside a `<think>`
    span and declares an empty answer; under `NEXUS_REPLY_SEPARATION=off`
    the same reply's draft IS parsed (T2c's pattern: the off position is
    the pre-C-38 parse; the request still carries W0b's seed unless
    NEXUS_INFERENCE_SEED is off too, N5). The corpus declares an unclosed span's empty
    answer instead of keeping the reasoning as the text.
R3  `collect_reply` assembles exactly what C-38's reference assembly does.
"""
from __future__ import annotations

import ast
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

import pytest

from tests._tracked import tracked

ROOT = Path(__file__).resolve().parent.parent

# What a script reaches. Network, socket and model-client modules, and the
# providers package (a script that holds a provider can ask it for text).
NET_MODULES = ("httpx", "requests", "urllib.request", "http.client", "aiohttp", "socket",
               "websocket", "websockets", "ollama", "openai", "anthropic", "litellm",
               "backend.providers")
STREAM_ATTRS = {"stream_chat", "stream_pieces"}
SEND_ATTRS = {"post", "stream", "request", "put", "patch", "send", "urlopen", "open",
              "putrequest", "create_connection", "open_connection"}
SPAWNERS = {"run", "Popen", "call", "check_call", "check_output", "system", "popen"}

# site -> (how many, why). A site is (file, kind, ...): an import of a
# module, a stream read, a send, a provider method call, a process call.
ANSWERED = {
    ("scripts/_as.py", "import", "httpx"): (1, "the daemon's tokeniser, for a count"),
    ("scripts/_as.py", "send", "ollama_tokeniser.count", "httpx.post"):
        (1, "a token count (num_predict 0, prompt_eval_count read); no text generated"),
    ("scripts/acceptance_governed_adaptation.py", "import", "httpx"): (1, "NEXUS's own HTTP routes, never a model"),
    ("scripts/acceptance_governed_adaptation.py", "send", "Runner.post", "self.c.post"):
        (1, "NEXUS's own HTTP routes; the app's loop takes the model text through reply_stream"),
    ("scripts/acceptance_governed_adaptation.py", "send", "main", "r.post"):
        (4, "the script's own Runner.post, above: NEXUS's routes, never a model"),
    ("scripts/citation_judge.py", "import", "backend.providers"):
        (3, "collect_reply, the reply chokepoint, with get_provider and the model map"),
    ("scripts/history_shape_harness.py", "import", "backend.providers"): (1, "the real provider for the loop"),
    ("scripts/history_shape_harness.py", "stream", "_Recording.stream_chat", "self.inner.stream_chat"):
        (1, "a provider wrapper the loop consumes, through reply_stream's fallback"),
    ("scripts/history_shape_replay.py", "import", "backend.providers"): (1, "the real provider, wrapped for the loop"),
    ("scripts/measure_retrieval.py", "import", "backend.providers"): (2, "embeddings and the model map; no text"),
    ("scripts/measure_vocabulary_compliance.py", "import", "backend.providers"):
        (4, "get_provider and the model map; collect_reply for the corpus, and for the arm under "
            "--arm-through-chokepoint (off by default)"),
    ("scripts/measure_vocabulary_compliance.py", "provider", "main", "provider.health"): (1, "a reachability check"),
    ("scripts/measure_vocabulary_compliance.py", "stream", "run_arm", "provider.stream_chat"):
        (1, "Ruling 2: the arm mirrors production extraction's own direct call"),
    ("scripts/predicate_similarity.py", "import", "backend.providers"): (2, "embeddings and the model map; no text"),
    ("scripts/probe_prefix.py", "import", "backend.providers"): (1, "embeddings for a prefix probe; no text"),
    ("scripts/replay_check.py", "import", "backend.providers"):
        (5, "the production parse, wire_body and the model map, to re-send and read a kept request"),
    ("scripts/replay_check.py", "import", "httpx"): (2, "its own daemon client: GET the tags, POST a kept body"),
    ("scripts/replay_check.py", "import", "socket"): (1, "is NEXUS serving: a port probe"),
    ("scripts/replay_check.py", "send", "HttpDaemon.chat", "httpx.stream"):
        (1, "re-sends the kept body with the recorded seed (wire_body); reply_stream would draw a new seed "
            "and rebuild the body; it parses with the chokepoint's own functions"),
    ("scripts/replay_check.py", "send", "captured_answers", "path.open"): (1, "a file (the capture), not the network"),
    ("scripts/replay_check.py", "send", "nexus_serving", "socket.create_connection"): (1, "a port probe; nothing sent"),
    ("scripts/who_outranks.py", "import", "backend.providers"): (2, "embeddings and the model map; no text"),
    # W3 part 4 — the deploy verifier speaks to NEXUS's own routes through
    # Caddy (health, the token, one turn, the record, the chain); the model
    # behind the deployment is whatever the profile runs, never asked here.
    ("scripts/verify_deploy.py", "import", "urllib.request"):
        (1, "NEXUS's own routes through Caddy: the deployment under test, never a model"),
    ("scripts/verify_deploy.py", "send", "Console.fetch", "urllib.request.urlopen"):
        (1, "every request of the verifier goes through this one method: /healthz, /chat (one turn, whose text "
            "the profile's provider answers), /lineage, /kairos/cycle, /chain/status"),
    # W4 part 4 — the simulated UAT drives the product from outside, as a
    # tester and an operator would (run/specs/W4.md D20): NEXUS's own HTTP
    # routes on the children it starts and NEXUS's own MCP server over stdio.
    # The model behind every child is the stub provider, in-process in that
    # child; no site here reaches a model or a host beyond this machine.
    # W10c — the operator's check of the interceptor against a real provider: one request, sent to an interceptor
    # the operator started, with the operator's own key from the environment (the suite runs it against the stub).
    ("scripts/intercept_smoke.py", "import", "http.client"):
        (1, "one request to a running interceptor on this machine, and its /health before and after"),
    ("scripts/intercept_smoke.py", "send", "_call", "conn.request"):
        (1, "the one sender: a request to the interceptor's address, never to a provider directly"),
    ("scripts/uat_simulate.py", "import", "http.client"):
        (1, "NEXUS's own routes on the console and tester processes the simulation starts, on loopback "
            "(or the host's own address, for the non-loopback refusal), never a model"),
    ("scripts/uat_simulate.py", "import", "socket"):
        (1, "a free loopback port per child; the streaming forwarder that stands in for an identity proxy, "
            "relaying bytes between the simulation's client and a NEXUS child; the host's own non-loopback "
            "address, read from a datagram socket's route (connect sends nothing)"),
    ("scripts/uat_simulate.py", "send", "Http.call", "conn.request"):
        (1, "every HTTP request of the simulation goes through this one method: NEXUS's routes, /chat "
            "among them, which the child's stub provider answers in-process"),
    ("scripts/uat_simulate.py", "send", "Forwarder._serve_one", "socket.create_connection"):
        (1, "the forwarder's one upstream connection per request, to the tester's NEXUS on loopback"),
    ("scripts/uat_simulate.py", "send", "mcp_rpc", "server.request"):
        (1, "NEXUS's own MCP server over stdio, JSON-RPC (scripts/bundle_smoke.py's client): every method "
            "the section sends goes through this one helper; never a model"),
    ("start.py", "import", "backend.providers"):
        (3, "W3: the backend-name reader (backend/providers/backends.py, standard library only), so the "
            "check, --serve and the interactive step refuse an unknown LLM_BACKEND as the factory does, "
            "and (part 2) the api settings reader (api_settings.py, standard library only) twice: the api "
            "backends' endpoint URL in model_server_target, and the key the model-server check sends as a "
            "header in _server_headers; no provider is constructed and no model is reached"),
    ("start.py", "import", "socket"): (1, "the exception types the model-server check names"),
    ("start.py", "import", "urllib.request"): (1, "the model-server check"),
    ("start.py", "send", "check_model_server", "opener.open"): (1, "one GET of the model list; no body, no text"),
}


def _module_of(name: str) -> "str | None":
    return next((m for m in NET_MODULES if name == m or name.startswith(m + ".")), None)


def model_read_sites(sources: dict[str, str]) -> Counter:
    """Every reach site in `sources`, counted."""
    sites: Counter = Counter()
    for path, src in sources.items():
        tree = ast.parse(src)
        imported = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    m = _module_of(a.name)
                    if m:
                        imported.add(m)
                        sites[(path, "import", m)] += 1
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                m = next((x for x in map(_module_of, [n.module] + [f"{n.module}.{a.name}" for a in n.names]) if x), None)
                if m:
                    imported.add(m)
                    sites[(path, "import", m)] += 1

        class V(ast.NodeVisitor):
            def __init__(self):
                self.stack, self.providers = [], [set()]

            def _scope(self, node):
                self.stack.append(node.name)
                self.providers.append(set(self.providers[-1]))
                self.generic_visit(node)
                self.providers.pop()
                self.stack.pop()

            visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope

            def visit_Assign(self, node):
                if isinstance(node.value, ast.Call) and ast.unparse(node.value.func).split(".")[-1] == "get_provider":
                    self.providers[-1] |= {t.id for t in node.targets if isinstance(t, ast.Name)}
                self.generic_visit(node)

            def visit_Attribute(self, node):
                if node.attr in STREAM_ATTRS:
                    sites[(path, "stream", ".".join(self.stack) or "<module>", ast.unparse(node))] += 1
                self.generic_visit(node)

            def visit_Call(self, node):
                qual, f = ".".join(self.stack) or "<module>", node.func
                if ast.unparse(f) == "getattr" and len(node.args) >= 2 \
                        and isinstance(node.args[1], ast.Constant) and node.args[1].value in STREAM_ATTRS:
                    sites[(path, "stream", qual, ast.unparse(node)[:60])] += 1
                name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
                if imported and name in SEND_ATTRS and (isinstance(f, ast.Attribute) or name == "urlopen"):
                    sites[(path, "send", qual, ast.unparse(f))] += 1
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and f.attr not in STREAM_ATTRS \
                        and (f.value.id in self.providers[-1] or f.value.id == "provider"):
                    sites[(path, "provider", qual, ast.unparse(f))] += 1
                if name in SPAWNERS and any(isinstance(c, ast.Constant) and isinstance(c.value, str)
                                            and any(w in c.value for w in ("curl", "ollama"))
                                            for a in node.args for c in ast.walk(a)):
                    sites[(path, "subprocess", qual, ast.unparse(f))] += 1
                self.generic_visit(node)

        V().visit(tree)
    return sites


def _script_sources() -> dict[str, str]:
    """Every tracked script and every tracked root .py (N1)."""
    population = [p for p in tracked("scripts") if p.suffix == ".py"]
    population += [p for p in tracked("*.py") if p.parent == ROOT]
    return {p.relative_to(ROOT).as_posix(): p.read_text(encoding="utf-8") for p in population}


def r1_problems(sources: dict[str, str]) -> list[str]:
    sites = model_read_sites(sources)
    problems = []
    for site, n in sorted(sites.items()):
        allowed = ANSWERED.get(site, (0, ""))[0]
        if n > allowed:
            problems.append(f"unanswered: {site} x{n} (answered for {allowed})")
    for site, (n, _why) in sorted(ANSWERED.items()):
        if sites.get(site, 0) < n:
            problems.append(f"gone: {site} answered x{n}, found x{sites.get(site, 0)}")
    return problems


def test_r1_every_script_model_read_is_answered_for():
    sources = _script_sources()
    sites = model_read_sites(sources)
    print(f"R1 population {len(sources)} files; reach sites, derived: {sorted(sites.items())}")
    assert len(sources) > 50 and sites, "non-vacuity"
    problems = r1_problems(sources)
    assert not problems, (
        "a script reaches a model, the network or a provider at a site nobody answered for, or an "
        "answered site is gone. Take a model's text through backend.providers.base.collect_reply, or "
        "answer for the site here with its reason:\n  " + "\n  ".join(problems))


def _planted_into(path: str, code: str) -> dict[str, str]:
    sources = _script_sources()
    sources[path] = sources.get(path, "") + "\n\n" + code
    return sources


R1C_PLANTED = {
    "a urllib opener POST": ("scripts/planted_probe.py",
        "from urllib.request import Request, build_opener\n"
        "def ask(url, body):\n"
        "    return build_opener().open(Request(url + '/api/chat', data=body, method='POST')).read()\n"),
    "an aliased httpx post": ("scripts/planted_probe.py",
        "from httpx import post as p\n"
        "def ask(url, body):\n    return p(url, json=body).json()['message']['content']\n"),
    # W11c: planted into a script the public tree keeps (bb3_run.py stays private with its pre-registration).
    "a client sending a built request": ("scripts/citation_judge.py",
        "def _planted(url, body):\n"
        "    import httpx\n"
        "    return httpx.Client().send(httpx.Request('POST', url, json=body)).json()\n"),
    "a bound alias of stream_chat": ("scripts/citation_judge.py",
        "async def _planted(provider):\n    read = provider.stream_chat\n    return [x async for x in read('m', [], 4096)]\n"),
    "getattr of stream_chat": ("scripts/citation_judge.py",
        "async def _planted(p):\n    return getattr(p, 'stream_chat')('m', [], 4096)\n"),
    "a new provider method": ("scripts/citation_judge.py",
        "async def _planted():\n    provider = get_provider()\n    return await provider.generate('m', 'q')\n"),
    "curl through subprocess": ("scripts/planted_probe.py",
        "import subprocess\ndef ask(body):\n"
        "    return subprocess.run(['curl', '-d', body, 'http://localhost:11434/api/chat'], capture_output=True)\n"),
}
# F5, the verifier's M9: a second, text-generating send planted INSIDE the
# answered function, under its answered key. Keyed by a set, the first
# version passed it; counted, it is one more than answered.
SECOND_SEND = ("        r = httpx.post(f\"{base_url}/api/generate\", json={\n",
               "        httpx.post(f\"{base_url}/api/generate\", json={\"model\": model, \"prompt\": text})"
               ".json()[\"response\"]\n"
               "        r = httpx.post(f\"{base_url}/api/generate\", json={\n")


@pytest.mark.parametrize("name", sorted(R1C_PLANTED))
def test_r1c_a_planted_form_fails_r1(name):
    path, code = R1C_PLANTED[name]
    problems = r1_problems(_planted_into(path, code))
    assert any(p.startswith("unanswered") and path in p for p in problems), (name, problems)


def test_r1c_a_second_send_inside_an_answered_function_fails_r1():
    sources = _script_sources()
    marker, planted = SECOND_SEND
    assert sources["scripts/_as.py"].count(marker) == 1, "the planting did not take"
    sources["scripts/_as.py"] = sources["scripts/_as.py"].replace(marker, planted, 1)
    problems = r1_problems(sources)
    assert any(p.startswith("unanswered") and "ollama_tokeniser.count" in p and "x2" in p for p in problems), problems


def test_r1c_an_answered_site_that_is_gone_fails_r1():
    """F5: "frozen both ways", witnessed in the gone direction."""
    sources = _script_sources()
    path = "scripts/replay_check.py"
    assert "httpx.stream(" in sources[path], "the planting did not take"
    sources[path] = sources[path].replace("httpx.stream(", "httpx_stream_renamed(", 1)
    problems = r1_problems(sources)
    assert any(p.startswith("gone") and "httpx.stream" in p for p in problems), problems


# ---------------------------------------------------------------------------
# R2 — the routed scripts, against a provider whose daemon puts the
# reasoning inline (a daemon older than Ollama 0.12.4, or LM Studio)
# ---------------------------------------------------------------------------

TAGGED = "qwen3.5:9b-q4_K_M"          # think_tags: ('<think>', '</think>')


class _Inline:
    """A provider with `stream_chat` only, so `reply_stream` takes its
    fallback and separates the inline span by the model's tag pair."""

    def __init__(self, pieces):
        self.pieces = pieces

    async def stream_chat(self, model, messages, num_ctx):
        for p in self.pieces:
            yield p


def _judge(pieces):
    from scripts import citation_judge as cj
    from tests.test_citation_judge import ROW
    order = cj.build_prompt(ROW, master_seed=7)[1]
    # The draft judges the FIRST presented item USED with a span the answer
    # contains; the stated verdict judges it UNUSED. Only the verdict counts.
    draft = json.dumps({"item": 1, "label": "USED", "span": ROW["answer"][:12]})
    verdict = json.dumps({"item": 1, "label": "UNUSED", "span": ""})
    stream = [p.format(draft=draft, verdict=verdict) for p in pieces]
    return asyncio.run(cj._judge_row(_Inline(stream), TAGGED, ROW, 7)), order


def test_r2_the_judge_parses_the_verdict_never_the_draft_in_the_reasoning(monkeypatch):
    monkeypatch.delenv("NEXUS_REPLY_SEPARATION", raising=False)
    out, order = _judge(["<think>Drafting:\n{draft}\n", "Checking.</think>\n\n", "{verdict}\n"])
    print(f"R2 separated: {out}")
    labels = [i["label"] for i in out["items"] if i["item"] == order[0]]
    assert labels == ["UNUSED"], (
        f"the judge's verdict should be its only judgement of the item; got {labels}")


def test_r2c_under_the_rollback_the_draft_is_parsed(monkeypatch):
    """T2c's pattern: `NEXUS_REPLY_SEPARATION=off` is the pre-C-38 path, so
    the same reply's draft reaches the parser. This is what the judge did on
    every daemon that put reasoning inline, before W1."""
    monkeypatch.setenv("NEXUS_REPLY_SEPARATION", "off")
    out, order = _judge(["<think>Drafting:\n{draft}\n", "Checking.</think>\n\n", "{verdict}\n"])
    labels = [i["label"] for i in out["items"] if i["item"] == order[0]]
    assert labels == ["USED", "UNUSED"], labels


def test_r2_a_judge_that_never_stated_a_verdict_is_declared(monkeypatch):
    monkeypatch.delenv("NEXUS_REPLY_SEPARATION", raising=False)
    out, _order = _judge(["<think>Drafting:\n{draft}\n", "still thinking"])
    assert out["items"] == [] and out["answer_empty"] is True
    assert out["answer_empty_reason"] == "the reply was cut off inside its thinking"


def test_r2_the_corpus_declares_an_unclosed_span_instead_of_keeping_it(monkeypatch):
    from scripts import measure_vocabulary_compliance as mvc
    monkeypatch.delenv("NEXUS_REPLY_SEPARATION", raising=False)
    corpus = asyncio.run(mvc.build_corpus(
        _Inline(["<think>", "A claim drafted in the reasoning: X causes Y"]), TAGGED, 1, 4096))
    print(f"R2 corpus: {corpus}")
    assert corpus[0]["text"] == "", "the reasoning was kept as the corpus text"
    assert corpus[0]["answer_empty_reason"] == "the reply was cut off inside its thinking"
    closed = asyncio.run(mvc.build_corpus(
        _Inline(["Preface. <think>R</think>", " The answer."]), TAGGED, 1, 4096))
    # The preface before the span is kept; the pre-W1 split kept only what
    # followed `</think>` and dropped it.
    text = closed[0]["text"]
    assert text.startswith("Preface.") and text.endswith("The answer."), closed
    assert "<think>" not in text and "R" not in text.split(), closed


# ---------------------------------------------------------------------------
# R3 — collect_reply is the loop's assembly
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pieces", [
    ["<think>R</think>", "The answer."],
    ["The answer, ", "no reasoning."],
    ["<think>", "cut off"],
])
def test_r3_collect_reply_equals_the_reference_assembly(pieces, monkeypatch):
    from backend.providers import base as PB
    from tests.test_c38_reply_separation import _collect
    monkeypatch.delenv("NEXUS_REPLY_SEPARATION", raising=False)
    expected, _chunks = _collect(_Inline(pieces), TAGGED)
    got, capture = asyncio.run(PB.collect_reply(
        _Inline(pieces), TAGGED, [{"role": "user", "content": "q"}], num_ctx=4096))
    assert got == expected
    assert capture is not None


# ===========================================================================
# W1 part 5c — the verifier's lens 3 findings
# ===========================================================================

class _Pieces:
    """A natively separating provider (Ollama 0.12.4 and later): the
    reasoning arrives as thinking pieces, the answer as content."""

    def __init__(self, pieces):
        self.pieces = pieces

    async def stream_pieces(self, model, messages, num_ctx, **_kw):
        for kind, text in self.pieces:
            yield kind, text

    async def stream_chat(self, model, messages, num_ctx):     # never taken while separating
        raise AssertionError("the native path was not taken")
        yield ""


class _Seeded(_Inline):
    """A provider that declares `takes_seed`, recording what it was sent."""
    takes_seed = True

    def __init__(self, pieces):
        super().__init__(pieces)
        self.seeds = []

    async def stream_chat(self, model, messages, num_ctx, seed=None):
        self.seeds.append(seed)
        for p in self.pieces:
            yield p


def _judge_with(provider):
    from scripts import citation_judge as cj
    from tests.test_citation_judge import ROW
    return asyncio.run(cj._judge_row(provider, TAGGED, ROW, 7)), cj.build_prompt(ROW, master_seed=7)[1]


def test_f4_on_the_native_path_the_judge_parses_the_verdict_never_the_thinking(monkeypatch):
    """F4: the operator's daemon separates natively; the first tests only
    used the inline fallback."""
    from tests.test_citation_judge import ROW
    monkeypatch.delenv("NEXUS_REPLY_SEPARATION", raising=False)
    draft = json.dumps({"item": 1, "label": "USED", "span": ROW["answer"][:12]})
    verdict = json.dumps({"item": 1, "label": "UNUSED", "span": ""})
    out, order = _judge_with(_Pieces([("thinking", f"Drafting:\n{draft}\n"), ("content", verdict + "\n")]))
    assert [i["label"] for i in out["items"] if i["item"] == order[0]] == ["UNUSED"]


@pytest.mark.parametrize("provider,reason", [
    (_Inline(["<think>Drafting", " still"]), "the reply was cut off inside its thinking"),
    (_Pieces([("thinking", "Drafting"), ("thinking", " still")]), "the reply contained only reasoning"),
])
def test_f4_an_empty_answer_is_declared_on_both_paths_with_the_paths_own_reason(provider, reason, monkeypatch):
    """The reason depends on the path, declared here: inline, an unclosed tag
    means the answer was cut off; native, the daemon separated the thinking
    and sent no content."""
    monkeypatch.delenv("NEXUS_REPLY_SEPARATION", raising=False)
    out, _order = _judge_with(provider)
    assert out["items"] == [] and out["answer_empty"] is True and out["answer_empty_reason"] == reason


def test_f3_the_judge_sends_a_seed_and_records_it_and_off_sends_none(monkeypatch):
    """F3: the seed collect_reply sends reaches the provider and the row; off
    sends none and says why."""
    monkeypatch.delenv("NEXUS_REPLY_SEPARATION", raising=False)
    verdict = json.dumps({"item": 1, "label": "UNUSED", "span": ""})
    monkeypatch.delenv("NEXUS_INFERENCE_SEED", raising=False)
    seeded = _Seeded([verdict + "\n"])
    out, _order = _judge_with(seeded)
    print(f"F3 seeds sent {seeded.seeds}; row {out.get('seed')} / {out.get('seed_note')}")
    assert len(seeded.seeds) == 1 and isinstance(seeded.seeds[0], int)
    assert out["seed"] == seeded.seeds[0]
    monkeypatch.setenv("NEXUS_INFERENCE_SEED", "off")
    off = _Seeded([verdict + "\n"])
    out_off, _order = _judge_with(off)
    assert off.seeds == [None] and out_off["seed"] is None
    assert "NEXUS_INFERENCE_SEED=off" in out_off["seed_note"]


def test_f3_the_corpus_records_each_requests_seed(monkeypatch):
    from scripts import measure_vocabulary_compliance as mvc
    monkeypatch.delenv("NEXUS_REPLY_SEPARATION", raising=False)
    monkeypatch.delenv("NEXUS_INFERENCE_SEED", raising=False)
    seeded = _Seeded(["An answer."])
    corpus = asyncio.run(mvc.build_corpus(seeded, TAGGED, 1, 4096))
    assert corpus[0]["seed"] == seeded.seeds[0] and isinstance(corpus[0]["seed"], int)


def _empty_answer_judgements():
    from tests.test_citation_judge import _judgements
    j = _judgements()
    j["rows"][1] = {"lineage_id": "L-2", "items": [], "discarded": 0, "answer_empty": True,
                    "answer_empty_reason": "the reply was cut off inside its thinking"}
    return j


def test_f2_a_judge_that_stated_no_answer_goes_to_a_human_never_into_the_sample():
    """F2 (the consequence): the row's groups are queued for a human under
    their own reason; none of them is in the agreement sample, which is
    where a judge-UNUSED row's groups would go."""
    from scripts.citation_queue import build_queue
    from tests.test_citation_judge import _key_doc
    _queue, key = build_queue(_key_doc(), _empty_answer_judgements(), seed=7, sample_n=3)
    reasons = {}
    for k in key:
        if k["lineage_id"] == "L-2":
            reasons[k["reason"]] = reasons.get(k["reason"], 0) + 1
    print(f"F2 queue reasons for the empty-answer row: {reasons}")
    assert reasons == {"judge_no_answer": 14}, reasons


def test_f2_the_scorer_counts_the_no_answer_items_apart():
    from scripts.citation_judge_score import score
    from scripts.citation_queue import build_queue
    from tests.test_citation_judge import _key_doc, _label
    judgements = _empty_answer_judgements()
    queue, key = build_queue(_key_doc(), judgements, seed=7, sample_n=3)
    out = score(_key_doc(), judgements, _label(queue, key, {}), {"entries": key, "seed": 7})
    assert out["false_negative"]["judge_no_answer_items"] == 14
    assert out["false_negative"]["agreement_sample"]["n"] == 0, "an empty-answer row was sampled"
    assert out["judge"]["judge_no_answer_rows"] == 1


# ---------------------------------------------------------------------------
# W1's close, must-fix M8 (the third verifier's lens 3, F7): F2 fixed one
# member of a class. A judge that did not judge (refused on the row, a reply
# that parsed to no line, an item it never named, a row missing from the
# file) is not a judge that found nothing (law 5). Each goes to a human under
# its own reason and is counted; the scorer holds its sample to labelled items
# too, so a key built by an older queue cannot score one as judge-UNUSED.
# Pre-fix (law 16), at 1a1a521, each queue test below read
# {'agreement_sample': 3}: three of the row's groups sampled as judge-UNUSED.
# ---------------------------------------------------------------------------

def _l2_reasons(judgements) -> dict:
    from scripts.citation_queue import build_queue
    from tests.test_citation_judge import _key_doc
    _queue, key = build_queue(_key_doc(), judgements, seed=7, sample_n=3)
    reasons = {}
    for k in key:
        if k["lineage_id"] == "L-2":
            reasons[k["reason"]] = reasons.get(k["reason"], 0) + 1
    return reasons


def _without_l2(j: dict) -> dict:
    j["rows"] = [r for r in j["rows"] if r["lineage_id"] != "L-2"]
    return j


def test_m8_a_row_the_judge_was_refused_on_goes_to_a_human():
    from tests.test_citation_judge import _judgements
    j = _without_l2(_judgements())
    j["refused_rows"] = [{"lineage_id": "L-2", "answer_model": "m", "reason": "judge == answerer"}]
    reasons = _l2_reasons(j)
    print(f"M8 refused row: {reasons}")
    assert reasons == {"judge_refused": 14}, reasons


def test_m8_a_reply_that_parsed_to_no_line_goes_to_a_human():
    from tests.test_citation_judge import _judgements
    j = _without_l2(_judgements())
    j["rows"].append({"lineage_id": "L-2", "items": [], "discarded": 0})    # stated, parsed to nothing
    reasons = _l2_reasons(j)
    print(f"M8 no line: {reasons}")
    assert reasons == {"judge_no_judgement": 14}, reasons


def test_m8_a_row_missing_from_the_judgements_goes_to_a_human():
    from tests.test_citation_judge import _judgements
    reasons = _l2_reasons(_without_l2(_judgements()))
    print(f"M8 missing row: {reasons}")
    assert reasons == {"judge_no_judgement": 14}, reasons


def test_m8_an_item_the_judge_never_named_goes_to_a_human_and_unsure_is_a_judgement():
    from tests.test_citation_judge import _judgements
    j = _judgements()
    l2 = next(r for r in j["rows"] if r["lineage_id"] == "L-2")
    l2["items"] = [it for it in l2["items"] if it["item"] != 14]            # never named
    l2["items"][0]["label"] = "UNSURE"                                       # named: a judgement
    l2["items"][1]["label"] = ""                                             # named with no label: none
    from scripts.citation_queue import build_queue
    from tests.test_citation_judge import _key_doc
    _queue, key = build_queue(_key_doc(), j, seed=7, sample_n=3)
    by_group = {k["group"]: k["reason"] for k in key if k["lineage_id"] == "L-2"}
    print(f"M8 by group: {by_group}")
    assert by_group.get("g14") == "judge_no_judgement" and by_group.get("g2") == "judge_no_judgement", by_group
    assert by_group.get("g1") in (None, "agreement_sample"), "an UNSURE label was taken as no judgement"
    assert sum(1 for r in by_group.values() if r == "agreement_sample") == 3


def test_m8_the_scorer_counts_them_apart_and_keeps_an_old_keys_unjudged_items_out_of_the_sample():
    """The scorer's own exclusion: a key from an older queue that sampled
    unjudged items (planted by renaming the reason back) is scored with those
    items out of the sample, and counted."""
    from scripts.citation_judge_score import score
    from scripts.citation_queue import build_queue
    from tests.test_citation_judge import _judgements, _key_doc, _label
    j = _without_l2(_judgements())
    j["refused_rows"] = [{"lineage_id": "L-2", "answer_model": "m", "reason": "judge == answerer"}]
    queue, key = build_queue(_key_doc(), j, seed=7, sample_n=3)
    out = score(_key_doc(), j, _label(queue, key, {}), {"entries": key, "seed": 7})
    assert out["false_negative"]["judge_refused_items"] == 14
    assert out["judge"]["judge_refused_rows"] == 1
    assert out["false_negative"]["agreement_sample"]["n"] == 0, "a refused row was sampled"
    old_key = [dict(k, reason="agreement_sample") if k["reason"] == "judge_refused" else k for k in key]
    out_old = score(_key_doc(), j, _label(queue, old_key, {}), {"entries": old_key, "seed": 7})
    print(f"M8 old key: sample n {out_old['false_negative']['agreement_sample']['n']}, "
          f"excluded {out_old['false_negative'].get('agreement_sample_unjudged_excluded')}")
    assert out_old["false_negative"]["agreement_sample"]["n"] == 0, "the scorer sampled items no judge labelled"
    assert out_old["false_negative"]["agreement_sample_unjudged_excluded"] == 14


def test_f7_an_arm_sends_nothing_for_an_entry_with_no_text(monkeypatch):
    """F7: production extraction makes no call for blank text; the arm skips
    the entry and counts it."""
    from scripts import measure_vocabulary_compliance as mvc
    calls = []

    class _Arm:
        async def stream_chat(self, model, messages, num_ctx):
            calls.append(messages[-1]["content"])
            yield "[]"
    corpus = [{"question": "q1", "text": ""}, {"question": "q2", "text": "Statins lower LDL."}]
    result = asyncio.run(mvc.run_arm(_Arm(), "m", "B", corpus, 4096))
    assert len(calls) == 1 and "Statins lower LDL." in calls[0]
    assert result["chunks_skipped_no_answer"] == 1 and result["chunks_sent"] == 1


def test_n6_the_arm_can_read_through_the_chokepoint_behind_its_flag(monkeypatch):
    """N6 (charter section 4): Ruling 2's alternative, built off behind a
    flag. On, the arm's draft inside a think span is not parsed."""
    from scripts import measure_vocabulary_compliance as mvc
    monkeypatch.delenv("NEXUS_REPLY_SEPARATION", raising=False)
    draft = json.dumps([{"subject": "x", "relation": "causes", "object": "y", "confidence": 0.9}])
    reply = ["<think>", draft, "</think>", "[]"]
    corpus = [{"question": "q", "text": "Statins lower LDL."}]
    off = asyncio.run(mvc.run_arm(_Inline(reply), TAGGED, "B", corpus, 4096))
    on = asyncio.run(mvc.run_arm(_Inline(reply), TAGGED, "B", corpus, 4096, through_chokepoint=True))
    assert on["read_through"] == "collect_reply" and off["read_through"].startswith("stream_chat")
    assert on["triples_total"] == 0, "the draft inside the think span was parsed"


def test_n3_the_corpus_keeps_both_answer_spans_and_untagged_prose(monkeypatch):
    """N3: the pre-W1 split kept a second span and cut a literal </think>
    out of a model's prose; both corrections, tested."""
    from scripts import measure_vocabulary_compliance as mvc
    monkeypatch.delenv("NEXUS_REPLY_SEPARATION", raising=False)
    two = asyncio.run(mvc.build_corpus(
        _Inline(["<think>alpha</think>First answer.", "<think>beta</think> More answer."]), TAGGED, 1, 4096))
    text = two[0]["text"]
    # The pre-W1 split kept everything after the FIRST </think>, the second
    # span included; the separator drops both spans and keeps both answers.
    assert "First answer." in text and "More answer." in text, two
    assert "<think>" not in text and "alpha" not in text and "beta" not in text, two
    plain = asyncio.run(mvc.build_corpus(
        _Inline(["Use the </think> tag to close a span."]), "phi4-mini:3.8b-q4_K_M", 1, 4096))
    assert plain[0]["text"] == "Use the </think> tag to close a span.", plain


@pytest.mark.parametrize("pieces", [
    ["<think>R</think>", "The answer."],
    ["The answer, ", "no reasoning."],
    ["<think>", "cut off"],
])
def test_n4_collect_reply_equals_the_reply_the_loop_passes_on(pieces, monkeypatch, tmp_path):
    """N4: against the loop's own assembly, not a test-side copy: the Reply
    run_turn hands to complete_turn, on the same pieces."""
    from backend.loop import agentic_loop
    from backend.providers import base as PB
    from tests.test_c38_reply_separation import THINKING_MODEL, _Chat, _plant, _turn
    from backend.memory.lineage import LineageStore
    monkeypatch.delenv("NEXUS_REPLY_SEPARATION", raising=False)
    monkeypatch.setenv("NEXUS_CALIBRATION_LOG", str(tmp_path / "cal.jsonl"))
    seen = []
    real = agentic_loop.complete_turn

    def capture(handle, reply, **kw):
        seen.append(reply)
        return real(handle, reply, **kw)
    monkeypatch.setattr(agentic_loop, "complete_turn", capture)
    store, lineage = _plant(tmp_path), LineageStore(tmp_path / "kb.db")
    _turn(store, lineage, _Chat(list(pieces)))
    store.close(); lineage.close()
    got, _capture = asyncio.run(PB.collect_reply(
        _Inline(list(pieces)), THINKING_MODEL, [{"role": "user", "content": "q"}], num_ctx=4096))
    assert seen and got == seen[0], (got, seen)

