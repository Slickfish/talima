"""Shared script setup — `.env` and the API token. Packet U.2 / U.3.

TWO DEFECTS, ONE SHAPE
    U.2  `acceptance_governed_adaptation.py` needed `--token` on a machine
         where `.env` already held the value, because the server loads `.env`
         and the script did not.
    U.3  With no token it sent `Authorization: Bearer ` and httpx refused:

             httpcore.LocalProtocolError: Illegal header value b'Bearer '

         A script whose stated purpose is documenting the auth flow failed
         with a transport error naming a byte string. It should stop and say
         where it looked.

    Both are "the script does not do what the server does". So the loader is
    imported from `backend.env` — the same functions the server calls, not a
    second implementation — and the token resolution is here so the next
    script inherits it rather than reinventing it.

NOT `backend.server`
    H.2 measured that importing that module migrated the operator's live
    `data/kb.db`. `backend.env` constructs nothing and opens nothing, which is
    why the loader moved there.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.env import env_file_candidates, load_env_files    # noqa: E402

TOKEN_ENV = "NEXUS_API_TOKEN"


class TokenNotFound(SystemExit):
    """No token anywhere. A `SystemExit` so a script stops with the message
    rather than a traceback — the operator is being told what to do, not shown
    where the program broke."""


def load_env() -> list[Path]:
    """Load `.env` exactly as the server does. Returns the files read."""
    return load_env_files()


def resolve_token(cli_token: str = "", *, required: bool = True) -> str:
    """`--token` -> `NEXUS_API_TOKEN` -> a named error.

    Call `load_env()` first, or the file's value will not be visible — the
    order is the point, and `require_token_or_exit` below does both so a
    caller cannot get it wrong.

    An EMPTY string is not a token. That is the whole of U.3: `Bearer ` with
    nothing after it is not an unauthenticated request, it is a malformed
    header, and httpx is right to refuse it. Better to stop here, where the
    message can name the two places we looked.
    """
    token = (cli_token or os.environ.get(TOKEN_ENV, "")).strip()
    if token:
        return token
    if not required:
        return ""
    looked = [f"  --token          (not given)" if not cli_token
              else f"  --token          (given but empty)"]
    for path in env_file_candidates():
        state = "read" if path.is_file() else "does not exist"
        looked.append(f"  {TOKEN_ENV} in {path}  ({state})")
    looked.append(f"  {TOKEN_ENV} in the environment  (not set)")
    raise TokenNotFound(
        f"\nNo API token found. Looked in, in order:\n"
        + "\n".join(looked)
        + f"\n\nEvery state-changing endpoint requires one. Either:\n"
          f"  set {TOKEN_ENV}=… in .env and restart the server, or\n"
          f"  pass --token with the value the server printed at startup.\n"
          f"(The server generates a per-process token when {TOKEN_ENV} is "
          f"unset, and `uvicorn --reload` regenerates it on every restart.)\n")


def require_token_or_exit(cli_token: str = "") -> str:
    """`.env` then the token, in that order. The one call a script needs."""
    load_env()
    return resolve_token(cli_token)


def auth_header(token: str) -> dict:
    """Never builds `Bearer ` with nothing after it (U.3)."""
    if not (token or "").strip():
        raise TokenNotFound(
            "refusing to send an empty bearer token; call "
            "require_token_or_exit() first")
    return {"Authorization": f"Bearer {token.strip()}"}


__all__ = ["TokenNotFound", "auth_header", "load_env", "require_token_or_exit",
           "resolve_token"]
