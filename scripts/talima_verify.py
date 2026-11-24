"""talima-verify — the standard's name for `nexus-verify`, the same verifier (W11b, run/specs/W11b.md D4).

Talima is the open record and verification standard; NEXUS is its reference engine. This file is the standard's name
for the engine's one verifier: it loads `nexus_verify.py` from the folder it is in and runs its `main()` under the
name `talima-verify`. Standard library only, like the verifier, so it runs isolated from a folder holding the two
files (alone, it names the missing file and exits 2):

    python -I talima_verify.py credential.json --identity identity.json

Everything it prints is what `nexus-verify` prints, with its own name in place of `nexus-verify` (argparse indents a
wrapped usage line by the name's length), apart from what it says, with exit 2, when the `nexus_verify.py` beside it
is missing, from before talima-verify, or cannot be run. An older main() hidden behind a wrapper that does not keep
its signature is not recognised: it is called, and its TypeError is printed as it is.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

VERIFIER = "nexus_verify.py"


def verifier():
    """The verifier beside this file, loaded by its path (an isolated interpreter puts no folder on the path). When
    it is not there, does not load, or has no main(), the error is named and the exit is 2: an installation error,
    never NOT VERIFIED's 1 and never a traceback."""
    path = Path(__file__).resolve().with_name(VERIFIER)
    if not path.is_file():
        print(f"talima-verify: cannot find {VERIFIER} beside {path.parent}: talima-verify is nexus-verify under the "
              f"standard's name and runs it from its own folder; put the two files together", file=sys.stderr)
        raise SystemExit(2)
    try:
        spec = importlib.util.spec_from_file_location("nexus_verify", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    except Exception as exc:                 # a truncated file's SyntaxError, or anything its import raises
        print(f"talima-verify: cannot load the {VERIFIER} beside it ({type(exc).__name__}: {exc}); use the two files "
              f"of one version", file=sys.stderr)
        raise SystemExit(2)
    if not callable(getattr(module, "main", None)):
        print(f"talima-verify: the {VERIFIER} beside it has no main(); use the two files of one version",
              file=sys.stderr)
        raise SystemExit(2)
    return module


def main(argv=None) -> int:
    """`nexus-verify`'s main() under the standard's name, after the verifier's own UTF-8 setting, as every script's
    main() starts (tests/test_subprocess_encoding.py). A verifier from before talima-verify (its main() takes no
    name) is named and the exit is 2: the two files must come from one version."""
    verify = verifier()
    force_utf8_stdio = getattr(verify, "force_utf8_stdio", lambda: None)
    force_utf8_stdio()
    try:
        inspect.signature(verify.main).bind(argv, name="talima-verify")
    except TypeError:                        # v1.0.0-uat's verifier: main(argv=None), no name
        print(f"talima-verify: the {VERIFIER} beside it is from before talima-verify (its main() takes no name); "
              f"use the two files of one version", file=sys.stderr)
        return 2
    except ValueError:                       # a callable with no signature to read: call it as it is
        pass
    return verify.main(argv, name="talima-verify")


if __name__ == "__main__":
    sys.exit(main())
