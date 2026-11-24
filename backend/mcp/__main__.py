"""MCP-2 — `python -m backend.mcp`: NEXUS as an MCP server over stdio.

One process, one person, one store. The HTTP app is never constructed and
no token setting is read. Configuration comes from the environment and the
`.env` the same loader the console uses reads (NEXUS_MCP_OWNER,
NEXUS_MCP_DATA_ROOT, NEXUS_MCP_HANDLE_TIMEOUT, NEXUS_LEARNING — frozen by
default here, `live` the opt-in). Everything construction prints goes to
stderr: stdout is the protocol channel.
"""
from __future__ import annotations

import contextlib
import logging
import os
import sys


def setup() -> tuple:
    """Load the environment, open the runtime and define the server. Returns
    `(runtime, server)`; nothing listens yet. Separated from `main` so a
    test can run it and show by introspection that no socket exists."""
    from backend.env import load_env_files
    from backend.mcp.config import OWNER_SETTING, data_root_choice, is_placeholder
    from backend.mcp.mcp_server import MCP_CONTRACT_VERSION, NexusRuntime, build_server
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    read = load_env_files()
    log = logging.getLogger("nexus.mcp")
    root, why = data_root_choice()
    log.info("NEXUS MCP: data folder %s (%s)", root, why)
    if is_placeholder(os.environ.get(OWNER_SETTING)):
        log.warning("NEXUS MCP: %s=%r is an unexpanded placeholder, not an identity: records are "
                    "unattributed", OWNER_SETTING, os.environ.get(OWNER_SETTING))
    with contextlib.redirect_stdout(sys.stderr):
        runtime = NexusRuntime.open()
        server = build_server(runtime)
    logging.getLogger("nexus.mcp").info(
        "NEXUS MCP: env files %s; store %s; owner %s; learning %s (%s); contract v%s",
        [p.as_posix() for p in read], runtime.services.lineage.conn.describe(),
        runtime.owner, runtime.learning_applied.get("value"), runtime.learning_applied.get("set_by"),
        MCP_CONTRACT_VERSION)
    return runtime, server


def main(argv: list[str] | None = None) -> int:
    """Serve over stdio. W2: when another process holds this store's lock,
    or its folder cannot be locked, say so on stderr (stdout is the
    protocol channel) and return the named exit, having opened nothing. W3:
    the same for a LLM_BACKEND that names no backend, or an api backend
    whose model is unset (exit 2)."""
    from backend.providers.backends import EXIT_UNKNOWN_BACKEND, BackendConfigError
    from backend.store import EXIT_STORE_CONFIG, BackendUnavailable, StoreConfigError
    from backend.store_lock import EXIT_STORE_LOCKED, StoreLocked, StoreLockUnavailable
    try:
        _runtime, server = setup()
    except (StoreLocked, StoreLockUnavailable) as e:
        print(f"NEXUS MCP: {e}. Nothing was opened.", file=sys.stderr, flush=True)
        return EXIT_STORE_LOCKED
    except (StoreConfigError, BackendUnavailable) as e:
        # W3b part 3 — NEXUS_STORE names no backend, or postgres without its
        # URL (or without the driver: the desktop bundle carries none), is
        # refused by name, having opened nothing: the store settings are
        # resolved before the lock and before any store connects.
        print(f"NEXUS MCP: {e}. Nothing was opened.", file=sys.stderr, flush=True)
        return EXIT_STORE_CONFIG
    except BackendConfigError as e:
        # W3 — a LLM_BACKEND that names no backend, or one whose model is
        # unset, is refused by name, having opened nothing (the provider is
        # resolved before any store).
        print(f"NEXUS MCP: {e}. Nothing was opened.", file=sys.stderr, flush=True)
        return EXIT_UNKNOWN_BACKEND
    server.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
