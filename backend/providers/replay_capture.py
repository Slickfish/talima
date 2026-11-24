"""W0b — what a later re-run of a turn will need, captured at the provider
boundary (RUN_WORKLIST.md W0b; the record carries it, the store keeps the
request).

A record sealed without its seed, the model's content digest, the exact
options and the exact request can never be re-run, and none of these can be
added afterwards. Replaying is NOT built here; only the capture.

THE SEED. `draw_seed()` is the one function that draws a sampling seed: 31
bits of OS entropy (`secrets`), fresh per answering request. Ollama's own
default is a fresh seed per request (its code's -1; llama-server draws one
from `std::random_device`), so a fresh OS-entropy seed is the same kind of
sampling as before; what changes is that the seed is now known. It is sent
only to a provider that declares `takes_seed`, and only on the answering
request: extraction requests carry none, exactly as before. `fixed_seed()`
is the one declared hook a test may use; nothing else sets a seed (an AST
guard holds it). `NEXUS_INFERENCE_SEED=off` sends none — the pre-W0b
request exactly — and the record says why its seed is NULL.

THE OPTIONS. A provider that sends options declares `request_config(model,
num_ctx, *, separate)`: the request body it sends minus the messages and the
seed, built by the same helper that builds the request, so the recorded
options are what was sent.

THE DIGEST. A provider that can name the content behind a tag declares
`served_model_digest(model)` -> (digest, reason). Ollama reads `/api/tags`
the first time a model serves in this process and when the model changes;
a re-pull while the process runs is not seen until then (declared).

THE ENVIRONMENT. `nexus_environment()`: the process's observable facts,
read once per process; a provider adds its own (`inference_environment()`,
Ollama's version). A fact that cannot be observed is ABSENT with its reason
under `<fact>_absent` — never guessed.

Every absent capture value carries its reason in `notes`, which the record
seals beside the fields.
"""
from __future__ import annotations

import contextlib
import functools
import os
import platform
import secrets
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

# W10c — the two switches and their readers live in `backend/memory/capture_settings.py` (a recorder with no
# memory engine reads them without loading a provider); the names here are the same objects.
from backend.memory.capture_settings import KEEP_SETTING, SEED_SETTING, keep_requests_enabled, seed_enabled
SEED_BITS = 31            # positive as a signed 32-bit int and as llama.cpp's uint32

# The capture's four record fields, in the order the record carries them.
CAPTURE_FIELDS = ("inference_seed", "model_digest", "inference_config_hash", "environment_hash")

# Keys the recorded options carry BESIDE the provider's request body, which
# the wire body does not: which provider and endpoint, and whether NEXUS
# separated the reply (C-38's switch decides how the answer, and so
# `answer_hash`, is read out of the same bytes; a replay must read it the
# same way).
SEPARATION_KEY = "nexus_reply_separation"
CONFIG_META_KEYS = ("provider", "endpoint", SEPARATION_KEY)
# Where each provider takes the seed in its body; None for a provider that
# takes none (W3's stub: `takes_seed` False, so its records carry no seed;
# Anthropic's Messages API takes none). The OpenAI-compatible endpoint takes
# it at the top, as LM Studio does.
SEED_PLACEMENT = {"ollama": ("options", "seed"), "lmstudio": ("seed",), "stub": None,
                  "openai": ("seed",), "anthropic": None}
# W3 part 2 — where a provider carries the system message when it is not in
# `messages`: Anthropic's Messages API takes it as the top-level `system`
# string (`anthropic.split_system`), and the kept messages are the request's
# text, so the rebuilt body splits it the same way.
SYSTEM_PLACEMENT = {"anthropic": "system"}

_fixed_seed: int | None = None


def draw_seed() -> int:
    """The one seed source: OS entropy, fresh per call."""
    if _fixed_seed is not None:
        return _fixed_seed
    return secrets.randbits(SEED_BITS)


@contextlib.contextmanager
def fixed_seed(value: int):
    """The ONE declared hook through which a test sets the seed."""
    global _fixed_seed
    previous, _fixed_seed = _fixed_seed, int(value)
    try:
        yield
    finally:
        _fixed_seed = previous


@dataclass(frozen=True)
class InferenceCapture:
    """What the answering request was, beside what it said. `notes` maps each
    absent field to its reason."""
    seed: int | None = None
    config: dict | None = None
    digest: str | None = None
    environment: dict | None = None
    notes: dict = field(default_factory=dict)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _run(args: list[str], cwd: Path | None = None) -> tuple[str | None, str]:
    try:
        cp = subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=5)
    except FileNotFoundError:
        return None, f"{args[0]} not found"
    except (OSError, subprocess.SubprocessError) as e:
        return None, f"{args[0]} failed: {type(e).__name__}"
    if cp.returncode != 0:
        return None, f"{args[0]} exited {cp.returncode}"
    return cp.stdout.strip(), ""


@functools.lru_cache(maxsize=1)
def nexus_environment() -> dict:
    """The process's observable facts, read once per process (the first
    answering turn). Absent facts carry their reason."""
    env: dict = {"python": platform.python_version(),
                 "python_implementation": platform.python_implementation(),
                 "os": sys.platform, "platform": platform.platform()}
    head, why = _run(["git", "rev-parse", "HEAD"], cwd=_repo_root())
    if head:
        env["nexus_commit"] = head
        dirty, _ = _run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=_repo_root())
        env["nexus_tree_modified"] = bool(dirty)
    else:
        env["nexus_commit_absent"] = why or "not a git checkout"
    gpus, why = _run(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
    if gpus:
        env["gpu"] = [line.strip() for line in gpus.splitlines() if line.strip()]
    else:
        env["gpu_absent"] = why or "nvidia-smi reported no GPU"
    # httpx serialises the wire body: 0.27 with spaced separators and ASCII
    # escapes, 0.28 compact and unescaped (requirements.txt allows both), so
    # the version says which bytes went (the daemon decodes either the same).
    try:
        import importlib.metadata as _md
        env["httpx"] = _md.version("httpx")
    except Exception as e:                           # declared, never guessed
        env["httpx_absent"] = f"version unreadable: {type(e).__name__}"
    env["provider_parallelism_absent"] = "not observable from NEXUS (a daemon-side setting)"
    return env


async def build_capture(provider, model: str, num_ctx: int, *, separate: bool,
                        seed: int | None, notes: dict) -> InferenceCapture:
    """After the answering stream: the options, the digest and the
    environment, each from what the provider declares. Never raises."""
    notes = dict(notes)
    config = None
    request_config = getattr(provider, "request_config", None)
    if callable(request_config):
        try:
            config = {**request_config(model, num_ctx, separate=separate),
                      SEPARATION_KEY: bool(separate)}
        except Exception as e:                       # declared, never raised into a turn
            notes["inference_config_hash"] = f"options unavailable: {type(e).__name__}"
    else:
        notes["inference_config_hash"] = "provider declares no request options"
    digest = None
    served = getattr(provider, "served_model_digest", None)
    if callable(served):
        try:
            digest, why = await served(model)
        except Exception as e:
            digest, why = None, f"digest unavailable: {type(e).__name__}"
        if not digest:                               # an empty digest is an absence, with its reason
            digest = None
            notes["model_digest"] = why or "the provider returned an empty digest"
    else:
        notes["model_digest"] = "provider exposes no content digest"
    environment = dict(nexus_environment())
    provider_env = getattr(provider, "inference_environment", None)
    if callable(provider_env):
        try:
            environment.update(await provider_env())
        except Exception as e:
            environment["provider_environment_absent"] = f"unavailable: {type(e).__name__}"
    else:
        environment["provider_environment_absent"] = "provider reports no runtime facts"
    environment["provider"] = type(provider).__name__
    return InferenceCapture(seed=seed, config=config, digest=digest,
                            environment=environment, notes=notes)


def wire_body(config: dict, messages: list[dict], seed: int | None) -> dict:
    """The request body a recorded config describes, rebuilt for a re-run:
    the provider's recorded body (the config minus `CONFIG_META_KEYS`), the
    kept messages, and the recorded seed where that provider takes it. The
    inverse of `request_config` plus the provider's `_payload`, pinned
    against `_payload` for each provider in tests/test_w0b_replay_check.py.
    Builds nothing that is not recorded; a provider whose seed placement is
    unknown is refused by name, and one that takes no seed is refused when
    the record carries one (none of its records does)."""
    import copy
    provider = config.get("provider")
    if provider not in SEED_PLACEMENT:
        raise ValueError(f"no seed placement is known for provider {provider!r}: {sorted(SEED_PLACEMENT)}")
    body = copy.deepcopy({k: v for k, v in config.items() if k not in CONFIG_META_KEYS})
    body["messages"] = [dict(m) for m in messages]
    system_key = SYSTEM_PLACEMENT.get(provider)
    if system_key:
        from backend.providers.anthropic import split_system
        system, rest = split_system(body["messages"])
        body["messages"] = rest
        if system is not None:
            body[system_key] = system
    if seed is not None:
        placement = SEED_PLACEMENT[provider]
        if placement is None:
            raise ValueError(f"provider {provider!r} takes no seed, and this record carries one ({seed})")
        *parents, leaf = placement
        target = body
        for key in parents:
            target = target.setdefault(key, {})
        target[leaf] = int(seed)
    return body
