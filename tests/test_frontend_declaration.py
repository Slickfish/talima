"""`/healthz` declares which page this process would serve.

WHY
    A provenance defect took four rounds, and three of them went on
    establishing what was being served. `FRONTEND` is absolute and derived
    from `__file__`, so the process always knew — and the answer appeared on
    no surface. "Is the fix deployed?" was an argument when it should have
    been a lookup.

    Same class as the unreachable `embedding_degraded` flag and the seal that
    reported itself unset: a fact the system holds and does not declare (I-6).

WHAT IS HASHED, AND WHY NOT THE RESPONSE
    The file on disk, not what `GET /` returns. `/` injects a token script
    into `<head>`, so a hash of the response would move with the token and be
    useless for comparing against a checkout. The published digest is
    reproducible with `sha256sum frontend/nexus_ui.html`.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.server import FRONTEND, create_app, create_services

TOKEN = "frontend-decl-token"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_API_TOKEN", TOKEN)
    services = create_services(
        kb_db_path=tmp_path / "kb.db", chroma_dir=tmp_path / "chroma",
        eval_log_path=tmp_path / "turn_metrics.jsonl",
        archive_db_path=tmp_path / "archive.db",
        snapshots_dir=tmp_path / "snapshots")
    return TestClient(create_app(services))


@pytest.mark.parametrize("route", ["/healthz", "/health"])
def test_both_health_routes_declare_the_frontend(client, route):
    """BOTH, because they are stacked decorators on one handler and an edit
    between them silently unbinds the canonical path — which is exactly what
    happened while this was being written. `/health` is the alias the UI
    polls; `/healthz` is canonical. A test on one would not have noticed."""
    body = client.get(route).json()
    assert "frontend" in body, f"{route} does not declare the page it serves"
    fe = body["frontend"]
    assert fe["path"] == str(FRONTEND)
    assert fe["exists"] is True


def test_the_digest_matches_the_file_on_disk(client):
    """Reproducible by hand — that is the whole point. An operator compares
    this against their checkout to answer "is the running server serving the
    code I think it is" without reading a single line of Python."""
    fe = client.get("/healthz").json()["frontend"]
    expected = hashlib.sha256(FRONTEND.read_bytes()).hexdigest()[:16]
    assert fe["sha256"] == expected
    assert fe["bytes"] == FRONTEND.stat().st_size


def test_the_digest_is_not_of_the_served_response(client):
    """`GET /` injects a token script into `<head>`, so the served response
    differs from the file and its hash moves with the token. Hashing the
    response would produce a number that never matches a checkout — a
    plausible, useless digest.

    THE INJECTION IS ASSERTED DIRECTLY, third instrument for this test. The
    first compared characters to bytes (em dashes made the string shorter
    than the file). The second compared served BYTES to `stat().st_size` —
    and failed on Windows by ~2KB the other way, because the checkout is
    CRLF, `read_text()` converts to LF, and the response is SHORTER than the
    file even with the script added. Both versions measured an encoding
    accident, not the injection. Substring presence measures the injection
    and nothing else, on any platform.
    """
    served = client.get("/").text
    file_text = FRONTEND.read_text(encoding="utf-8")
    # The marker is the ASSIGNMENT WITH THE VALUE, deliberately not the bare
    # identifier: the page's auth shim itself reads `window.NEXUS_TOKEN`, so
    # the bare name is in the file too and would make this vacuous. The next
    # assertion pins that distinction so a shim rewrite cannot silently turn
    # this test into one that passes with no injection at all.
    marker = f'window.NEXUS_TOKEN={json.dumps(TOKEN)};'
    assert "window.NEXUS_TOKEN" in file_text, (
        "the auth shim no longer reads window.NEXUS_TOKEN — the bare-name/"
        "assignment distinction this test leans on has moved; re-derive the "
        "marker")
    assert marker in served, "the token script was not injected"
    assert marker not in file_text, (
        "the token value is IN THE FILE on disk — that is a leaked secret, "
        "not a test failure to relax")
    fe = client.get("/healthz").json()["frontend"]
    assert fe["sha256"] != hashlib.sha256(served.encode("utf-8")).hexdigest()[:16]


def test_the_digest_tracks_edits_rather_than_being_cached(client, monkeypatch,
                                                          tmp_path):
    """NON-VACUITY, and the property that makes it worth having.

    `GET /` re-reads the file on every request, so a `git pull` takes effect
    on the next page load with no restart. A digest computed once at boot
    would report the version that was current then — the wrong answer to "is
    the fix deployed", and confidently wrong.
    """
    import backend.server as srv
    before = client.get("/healthz").json()["frontend"]

    edited = tmp_path / "nexus_ui.html"
    edited.write_text(FRONTEND.read_text(encoding="utf-8") + "\n<!-- probe -->\n",
                      encoding="utf-8")
    monkeypatch.setattr(srv, "FRONTEND", edited)
    after = client.get("/healthz").json()["frontend"]

    assert after["path"] == str(edited)
    assert after["sha256"] != before["sha256"], (
        "the digest did not move when the file did — it is cached, and a "
        "cached digest answers a question nobody asked")
    assert after["bytes"] > before["bytes"]


def test_a_missing_page_is_declared_not_omitted(client, monkeypatch, tmp_path):
    """`GET /` falls back to a JSON stub when the page is absent. A status
    surface that dropped the key would leave the operator unable to tell
    "no page" from "an older build with no declaration"."""
    import backend.server as srv
    monkeypatch.setattr(srv, "FRONTEND", tmp_path / "does-not-exist.html")
    fe = client.get("/healthz").json()["frontend"]
    assert fe["exists"] is False
    assert fe["path"].endswith("does-not-exist.html")
    assert fe.get("note"), "absence is reported without saying what it means"
