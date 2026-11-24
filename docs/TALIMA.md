# Talima

Talima is the open record and verification standard; NEXUS is its reference engine, as OpenTelemetry names a
standard and Jaeger one of its implementations. Today the standard is exactly the formats NEXUS already writes, and
this page names each one by its identifier: `nexus-verify` checks the credentials and receipts, and any RFC 9421
verifier the signed requests. The marks are in `docs/brand/`.

## The formats

| Identifier | What it is | Checked by |
|---|---|---|
| `nexus.credential` | A decision credential: one sealed record of a turn, the fields it discloses, a salted commitment for each other field, the Merkle inclusion proof that places the record in a sealed block, and the instance's Ed25519 signatures | `nexus-verify credential.json --identity identity.json` |
| `nexus.commitment` | A commitment receipt: content committed now by a salted hash, held under a seal, revealed later | `nexus-verify --reveal receipt.json --content <file> --salt <hex> --identity identity.json` |
| `nexus.action` | An action receipt: a request NEXUS sent for a person, recorded before it left and sealed; or a request the client reported (`nexus_record_action`), which the verifier says NEXUS did not see happen | `nexus-verify --action action.json --identity identity.json` |
| `nexus.chain` | A chain document: a producer's blocks in order, each with its header, the hashes it sealed and its signature, so that the links between blocks can be checked | `nexus-verify --chain chain.json --identity identity.json` |
| the RFC 9421 label `nexus` | The HTTP message signature on each request NEXUS sends for a person, when `NEXUS_SIGN_REQUESTS=on` | any RFC 9421 verifier, with the key `GET /identity` serves |
| `GET /identity` | The instance's public keys: every key it has used, with its active period | read by the verifier (`--identity identity.json`) |

Each credential and receipt carries `"version": 1`. Every block signature in them signs the same statement: the
bytes `NEXUS-BLOCK-V1` and a zero byte, the block's index as eight big-endian bytes, and its 32-byte hash.

## The specification

`docs/TALIMA_SPEC.md` specifies version 1 so that it can be implemented without reading NEXUS: the record and its
canonical encoding, the commitments, the Merkle trees, the blocks and their chain, the keys and signatures, each
document above, and how each is verified. Every rule in it is a producer's, and the specification says where a
verifier holds each (a chain's rules where a chain is shown, a record's as far as its fields are disclosed, a
key's period as far as a signature's label allows): the project's tests break each rule in turn, in a second producer written from the specification alone (`conformance/minimal_producer.py`,
the Python standard library only), and require the failure the specification names.

## The rule

*v1 identifiers keep the `nexus.*` spelling; a later version may add `talima.*` identifiers, and every verifier then
accepts both.* No identifier above is renamed: a change is a new format version, and the old version is still
verified. `tests/test_tb_frozen_identifiers.py` holds every string and bytes literal naming `nexus` in the
backend's and the scripts' Python and in the desktop bundle's manifest, each with its class and its reason, and
each file's in their source order: it fails on one added, removed or respelled, on a rename at one site, and on two
swapped between sites, until it is answered for. It does not read docstrings (the MCP tools' descriptions are
docstrings); Python identifiers (the MCP tools are registered by their functions' names, and
`tests/test_w2_bundle.py` holds the served tools equal to the manifest's); names a program builds at run time; or
files that are not Python, in the scripts or outside them, and Python outside the backend and the scripts
(`start.py`, and the golden fixtures' generator, kept with the development records). Every `NEXUS_*` setting in
the project's settings block (the one `documented_settings()` in `tests/test_dotenv_settings_take_effect.py` reads)
is also a held literal, and that test holds the block both ways to its probes. A setting named only in the files
this pass does not read is outside that block and has no probe: it is held only where another test reads it (the
deploy tests read some), or by nothing. The order is kept among the held literals only: one exchanged with a literal
that names no nexus is not seen.

## The verifier's two names

`talima-verify` is the standard's name for the one verifier, and `nexus-verify` the engine's. They are the same
program: `scripts/talima_verify.py` runs `scripts/nexus_verify.py` from its own folder, standard library only,
and each prints its own name and nothing else differently (argparse indents a wrapped usage line by the name's
length). `nexus_verify.py` runs from an empty folder holding only the files it checks; `talima_verify.py` needs
the `nexus_verify.py` of its own version beside it, and alone, or beside an older one, it says which and exits 2:

    python -I talima_verify.py credential.json --identity identity.json

The release workflow builds both names for Linux, Windows and macOS.
