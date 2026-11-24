"""W10c (run/specs/W10c.md S7) — `talima-intercept`: the interceptor's command line (`python -m backend.intercept`).

    talima-intercept [serve] [--host H] [--port P]   run the interceptor (the default)
    talima-intercept list [--limit N]                the recorded exchanges, newest first
    talima-intercept verify                          the chain, every record and every sealed action, re-checked
    talima-intercept identity [--out FILE]           the instance's public keys, for talima-verify to pin
    talima-intercept credential <lineage id> [--disclose f,g] [--out FILE]
    talima-intercept receipt <action id> [--out FILE]
    talima-intercept chain [--out FILE]              the chain as one document: its blocks, links, seals and signatures

    talima-verify credential.json --identity identity.json
    talima-verify --action receipt.json --identity identity.json
    talima-verify --chain chain.json --identity identity.json

Every command but `serve` opens the store itself, so it runs while the interceptor is stopped: one process writes a
store (the single-writer lock, exit 7). Each first marks `incomplete` any exchange a stopped process left unfinished;
`verify`, `identity`, `credential`, `receipt` and `chain` then seal what is unsealed. What they write is
checked by `talima-verify`, which needs neither this program nor the store.

Exits: 0 done; 1 `verify` found something that does not verify, or a credential was refused; 2 the settings are
refused (named on stderr; nothing opened); 7 the store is held by another process.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXIT_OK, EXIT_FAILED = 0, 1


def _program() -> str:
    name = Path(sys.argv[0]).stem.lower() if sys.argv and sys.argv[0] else ""
    return "talima-intercept" if name.startswith("talima-intercept") or name.startswith("talima_intercept") \
        else "python -m backend.intercept"


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog=_program(), description="The Talima interceptor: an agent harness's model "
                                 "endpoint, recording every exchange on its way to the provider.")
    sub = ap.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="run the interceptor (the default)")
    for p, default in ((ap, None), (serve, argparse.SUPPRESS)):     # given before or after `serve`, never reset by it
        p.add_argument("--host", default=default, help="the address to listen on (default 127.0.0.1)")
        p.add_argument("--port", type=int, default=default, help="the port to listen on (default 8790)")
    listing = sub.add_parser("list", help="the recorded exchanges, newest first")
    listing.add_argument("--limit", type=int, default=20)
    sub.add_parser("verify", help="re-check the chain, every record and every sealed action")
    cred = sub.add_parser("credential", help="a record's credential, for talima-verify")
    cred.add_argument("lineage_id")
    cred.add_argument("--disclose", default="", help="fields to disclose with their values, comma-separated")
    cred.add_argument("--out", default=None)
    receipt = sub.add_parser("receipt", help="an action's receipt, for talima-verify")
    receipt.add_argument("action_id")
    receipt.add_argument("--out", default=None)
    ident = sub.add_parser("identity", help="the instance's public keys, for talima-verify to pin")
    ident.add_argument("--out", default=None)
    whole = sub.add_parser("chain", help="the chain as one document, for talima-verify --chain")
    whole.add_argument("--out", default=None)
    return ap


def _emit(doc: dict, out: "str | None") -> None:
    text = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print(f"written: {out}")
    else:
        sys.stdout.write(text)


def main(argv=None) -> int:
    from backend.env import load_env_files
    from backend.intercept import config as _config
    from backend.store_lock import EXIT_STORE_LOCKED, StoreLocked, StoreLockUnavailable
    args = _parser().parse_args(argv)
    command = args.command or "serve"
    load_env_files()
    try:
        config = _config.load(host=getattr(args, "host", None), port=getattr(args, "port", None),
                              need_upstream=command == "serve")
        if command == "serve" and config.inject:         # asked for, so it must be here: before anything opens
            from backend.intercept.app import link
            link(config)
    except _config.ConfigRefused as e:
        print(f"{_program()}: {e}", file=sys.stderr)
        return _config.EXIT_CONFIG
    from backend.intercept.recorder import Recorder
    try:
        recorder = Recorder.open_at(config.data_root)
    except _config.ConfigRefused as e:
        print(f"{_program()}: {e}", file=sys.stderr)
        return _config.EXIT_CONFIG
    except (StoreLocked, StoreLockUnavailable) as e:
        print(f"{_program()}: {e}", file=sys.stderr)
        return EXIT_STORE_LOCKED
    if command == "serve":
        return _serve(config, recorder)
    try:
        return _read(command, args, recorder)
    finally:
        recorder.close()


def _serve(config, recorder) -> int:
    import uvicorn

    from backend.intercept.app import build
    app = build(config, recorder)
    print(f"{_program()}: recording into {config.data_root}; forwarding to {config.upstream}; "
          f"listening on http://{config.host}:{config.port}", file=sys.stderr)
    # One process, one writer. The peer is the socket's own (W4): no proxy header rewrites it. No header of the
    # server's own is added to a response the upstream wrote.
    uvicorn.run(app, host=config.host, port=config.port, workers=1, proxy_headers=False, server_header=False,
                date_header=False, access_log=False, log_level="info")
    return EXIT_OK


def _read(command: str, args, recorder) -> int:
    from backend.memory.credentials import CredentialRefused
    # This process holds the store, so no interceptor is serving: an exchange still `requested` is one a process
    # left unfinished when it stopped (the sweep `serve` makes at its start).
    recorder.sweep_unfinished()
    if command == "list":
        rows = recorder.listing(limit=max(1, args.limit))
        for row in rows:
            print(json.dumps(row, sort_keys=True))
        print(f"{len(rows)} exchange(s) shown", file=sys.stderr)
        return EXIT_OK
    if command == "verify":
        recorder.seal()
        result = recorder.verify()
        _emit(result, None)
        return EXIT_OK if result["ok"] else EXIT_FAILED
    if command == "identity":
        recorder.seal()                                  # the block that announces a new key, if none has yet
        _emit(recorder.identity(), args.out)
        return EXIT_OK
    try:
        recorder.seal()
        if command == "credential":
            disclose = tuple(f.strip() for f in args.disclose.split(",") if f.strip())
            _emit(recorder.credential(args.lineage_id, disclose=disclose), args.out)
        elif command == "chain":
            _emit(recorder.chain_document(), args.out)
        else:
            _emit(recorder.receipt(args.action_id), args.out)
    except CredentialRefused as e:
        print(f"{_program()}: {e}", file=sys.stderr)
        return EXIT_FAILED
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
