"""W10c — the interceptor against a real provider: the check an operator runs with their own key.

The project's suite calls no provider (it holds no key), so what it proves about the interceptor it proves against a
stub (`backend/intercept/stub_upstream.py`). This script is the other half, and only you can run it: it sends ONE
small request through an interceptor you have started and says what came back.

    1. start the interceptor against the provider (docs/INTERCEPTOR.md):
           NEXUS_INTERCEPT_UPSTREAM=https://api.anthropic.com
           NEXUS_INTERCEPT_DATA_ROOT=<a folder of its own>
           python -m backend.intercept
    2. in another terminal, with your key in the environment (it is read from there, never from the command line,
       never printed, and never stored by this script or by the interceptor):
           ANTHROPIC_API_KEY=...   python scripts/intercept_smoke.py
           OPENAI_API_KEY=...      python scripts/intercept_smoke.py --protocol openai --model <a model you can call>
    3. stop the interceptor, then `python -m backend.intercept list` and `verify`.

It costs one request of a few tokens. Exit 0: the provider answered through the interceptor and the interceptor
recorded one more exchange. Exit 1: it did not, and the reason is printed. Exit 2: nothing was sent (no key in the
environment, or no interceptor at the address).
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import force_utf8_stdio  # noqa: E402
from _env import auth_header, load_env  # noqa: E402

PROTOCOLS = {
    "anthropic": {"path": "/v1/messages", "key": "ANTHROPIC_API_KEY", "model": "claude-haiku-5-5"},
    "openai": {"path": "/v1/chat/completions", "key": "OPENAI_API_KEY", "model": None},
}
ASK = "Reply with the single word: ok"
TIMEOUT_S = 120.0


def _call(base: str, method: str, path: str, *, body: "bytes | None" = None, headers: "dict | None" = None):
    """(status, body bytes) of one request to the interceptor; `http.client` reads no proxy setting."""
    parts = urlsplit(base)
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=TIMEOUT_S)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def _health(base: str) -> "dict | None":
    try:
        status, raw = _call(base, "GET", "/health")
        doc = json.loads(raw.decode("utf-8", "replace")) if status == 200 else None
        return doc if isinstance(doc, dict) and doc.get("recorder") == "talima-intercept" else None
    except (OSError, ValueError, http.client.HTTPException):
        return None


def _request(protocol: str, model: str, key: str) -> "tuple[bytes, dict]":
    if protocol == "openai":
        doc = {"model": model, "stream": True, "messages": [{"role": "user", "content": ASK}]}
        return json.dumps(doc).encode("utf-8"), {"content-type": "application/json", **auth_header(key)}
    doc = {"model": model, "max_tokens": 16, "stream": True, "messages": [{"role": "user", "content": ASK}]}
    return json.dumps(doc).encode("utf-8"), {"content-type": "application/json", "x-api-key": key,
                                             "anthropic-version": "2023-06-01"}


def _said(protocol: str, raw: bytes) -> "tuple[str, str, bool]":
    """(the model the stream names, the text it carried, whether its last event arrived)."""
    model, text, ended = "", [], False
    for line in raw.decode("utf-8", "replace").splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            ended = True
            continue
        try:
            doc = json.loads(data)
        except ValueError:
            continue
        if not isinstance(doc, dict):
            continue
        if protocol == "openai":
            model = doc.get("model") or model
            for choice in doc.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content") if isinstance(choice, dict) else None
                if isinstance(piece, str):
                    text.append(piece)
        else:
            model = (doc.get("message") or {}).get("model") or model
            delta = doc.get("delta") or {}
            if doc.get("type") == "content_block_delta" and isinstance(delta.get("text"), str):
                text.append(delta["text"])
            ended = ended or doc.get("type") == "message_stop"
    return model, "".join(text), ended


def main(argv=None) -> int:
    force_utf8_stdio()
    load_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--base-url", default="http://127.0.0.1:8790", help="where the interceptor listens")
    ap.add_argument("--protocol", choices=sorted(PROTOCOLS), default="anthropic")
    ap.add_argument("--model", default=None, help="the model to ask (anthropic: a small one by default)")
    args = ap.parse_args(argv)
    spec = PROTOCOLS[args.protocol]
    model = args.model or spec["model"]
    key = (os.environ.get(spec["key"]) or "").strip()
    if not key:
        print(f"intercept-smoke: no key: {spec['key']} is not set in the environment. Nothing was sent.")
        return 2
    if not model:
        print("intercept-smoke: no model: give --model with a model your key can call. Nothing was sent.")
        return 2
    before = _health(args.base_url)
    if before is None:
        print(f"intercept-smoke: no interceptor answered at {args.base_url}/health. Start it first "
              "(docs/INTERCEPTOR.md). Nothing was sent.")
        return 2
    body, headers = _request(args.protocol, model, key)
    try:
        status, raw = _call(args.base_url, "POST", spec["path"], body=body, headers=headers)
    except (OSError, http.client.HTTPException) as e:
        print(f"intercept-smoke: FAIL: the request did not complete ({type(e).__name__})")
        return 1
    after = _health(args.base_url) or {}
    named, text, ended = _said(args.protocol, raw)
    recorded = after.get("recorded", 0) - before.get("recorded", 0)
    print(f"intercept-smoke: {args.protocol} through {args.base_url}: HTTP {status}; the response names the model "
          f"{named or '(none)'}; it said {text.strip()[:60]!r}; its last event {'arrived' if ended else 'did not arrive'}")
    print(f"intercept-smoke: the interceptor recorded {recorded} more exchange(s) "
          f"(recorded {before.get('recorded')} -> {after.get('recorded')}, incomplete {after.get('incomplete')})")
    if status != 200:
        print("intercept-smoke: the provider's own answer, as relayed: "
              + raw.decode("utf-8", "replace").replace(key, "<the key>")[:400])
    ok = status == 200 and ended and recorded == 1
    print("intercept-smoke: PASS. Stop the interceptor, then: python -m backend.intercept list; "
          "python -m backend.intercept verify" if ok else "intercept-smoke: FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
