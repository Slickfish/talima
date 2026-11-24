"""W9 (run/specs/W9.md, D1 to D3) — the instance's Ed25519 key.

The key says WHO sealed, never WHEN: a priority claim needs a time a stranger
trusts, which is anchoring's (D7). One key per instance, made at its first use
into the data root beside kb.db as `instance_key.json`: the 32-byte seed, the
public key and when it was made. On POSIX the file is owner-only (0600); on
Windows it takes its folder's access list. It is never written to `.env`, a
log, a record or a prompt, and nothing but this module reads the seed.

Signing uses `cryptography` (pinned in requirements.txt, D2). Verifying never
needs it: `scripts/nexus_verify.py` carries its own RFC 8032 check.

A key file whose public half is not its seed's, or that cannot be read, is
refused by name (`KeyMismatch`) and never repaired or replaced: a silent new
key would sign as someone else. This is not `backend/identity.py`, which is
W4's boundary for who a REQUEST is from.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

logger = logging.getLogger(__name__)

KEY_FILE = "instance_key.json"
# Where the key lands beside the default store (`backend/kb/schema.py`'s DEFAULT_DB, `data/kb.db`): named so
# the writer ratchet (tests/_writers.py), the ignore rules and the data/ sentinel see it. Never in git.
DEFAULT_KEY_PATH = "data/instance_key.json"
ALGORITHM = "ed25519"


class KeyMismatch(RuntimeError):
    """The key file cannot be the instance's key; nothing was signed and the file is left as it was."""


@dataclass(frozen=True)
class InstanceKey:
    public_key: bytes
    created_at: float
    path: Path
    _private: Ed25519PrivateKey = field(repr=False, compare=False)
    # W9's delta check 3 — the archive's next block index when the key was made, beside the store it was made
    # for: where the instance moved to it. None for a key made with no archive at hand.
    made_at_block: "int | None" = None

    @property
    def key_id(self) -> str:
        return hashlib.sha256(self.public_key).hexdigest()

    def sign(self, message: bytes) -> bytes:
        return self._private.sign(bytes(message))

    def identity(self) -> dict:
        """What `GET /identity` serves: the public half, nothing else."""
        return {"algorithm": ALGORITHM, "public_key": self.public_key.hex(), "key_id": self.key_id,
                "created_at": self.created_at}


_cache: dict[str, InstanceKey] = {}
_lock = threading.Lock()


def _public(private: Ed25519PrivateKey) -> bytes:
    return private.public_key().public_bytes(encoding=serialization.Encoding.Raw,
                                             format=serialization.PublicFormat.Raw)


def _read(path: Path) -> InstanceKey:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(data["seed"]))
        stored, created = bytes.fromhex(data["public_key"]), float(data["created_at"])
        made = data.get("made_at_block")
        if made is not None and (isinstance(made, bool) or not isinstance(made, int) or made < 0):
            raise ValueError("made_at_block is not a block index")
    except OSError:
        raise                                       # a file the process cannot read is `key_unreadable`, not a mismatch
    except (ValueError, KeyError, TypeError) as e:
        raise KeyMismatch(f"{path.name} cannot be read as an Ed25519 key ({type(e).__name__}); nothing was "
                          "signed, and the file is not replaced") from None
    if data.get("algorithm") != ALGORITHM or _public(private) != stored:
        raise KeyMismatch(f"{path.name}'s public key is not its seed's; nothing was signed, and the file is "
                          "not replaced")
    return InstanceKey(public_key=stored, created_at=created, path=path, _private=private, made_at_block=made)


def _create(path: Path, now: float, made_at_block: "int | None" = None) -> None:
    """Write a new key file if there is none, atomically: the body goes to a
    file of its own first and is linked into place only if `path` is absent,
    so a second process never reads half a key, and an existing key is never
    overwritten."""
    private = Ed25519PrivateKey.generate()
    seed = private.private_bytes(encoding=serialization.Encoding.Raw, format=serialization.PrivateFormat.Raw,
                                 encryption_algorithm=serialization.NoEncryption())
    fields = {"algorithm": ALGORITHM, "seed": seed.hex(), "public_key": _public(private).hex(), "created_at": now}
    if made_at_block is not None:
        fields["made_at_block"] = made_at_block
    body = json.dumps(fields, sort_keys=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    scratch = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    fd = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(scratch, path)
        except FileExistsError:
            pass                                    # another process made it first: theirs is the key
        except OSError:
            # A filesystem without hard links (FAT, some network shares):
            # create the file itself exclusively, so a key is still never
            # overwritten; the single-writer lock (W2) keeps two NEXUS
            # processes off one store, so a half-written read cannot race it.
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                pass
            else:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(body)
                    f.flush()
                    os.fsync(f.fileno())
    finally:
        try:
            scratch.unlink(missing_ok=True)
        except OSError as e:                        # held by another process (an antivirus, on Windows): the key
            logger.warning("the key's scratch file %s could not be removed now (%s): it holds a copy of the private "
                           "key; remove it by hand once nothing holds it", scratch.name, type(e).__name__)


def _next_block(chain) -> "int | None":
    if chain is None:
        return None
    latest = chain.latest()
    return latest.index + 1 if latest is not None else 0


def load_or_create(path, *, now: float | None = None, chain=None) -> InstanceKey:
    """The instance's key at `path`: read it, or make it at the first use.
    Made beside `chain` (the archive it signs for), the file records the
    archive's next block index (`made_at_block`): where the instance moved to
    this key, which closes the previous key's range (W9's delta check 3)."""
    path = Path(path)
    with _lock:
        cached = _cache.get(str(path.resolve()))
        if cached is not None:
            return cached
        if not path.exists():
            _create(path, time.time() if now is None else now, _next_block(chain))
        key = _read(path)
        _cache[str(path.resolve())] = key
        return key


# ---------- W9 part 2: what is signed, and the instance's keys over time ----------

STATEMENT_PREFIX = b"NEXUS-BLOCK-V1\x00"


def block_statement(index: int, block_hash: str) -> bytes:
    """The bytes an instance signs for a block: a fixed prefix (so a block
    signature is never a signature over anything else), the block's index as
    eight big-endian bytes, and its 32-byte hash. `scripts/nexus_verify.py`
    builds the same bytes from a credential."""
    if not (isinstance(index, int) and 0 <= index < 2 ** 63):
        raise ValueError(f"a block index is a non-negative integer, not {index!r}")
    raw = bytes.fromhex(block_hash)
    if len(raw) != 32:
        raise ValueError("a block hash is 32 bytes")
    return STATEMENT_PREFIX + index.to_bytes(8, "big") + raw


def signer_for(path, chain):
    """`(index, block_hash) -> (key_id, signature_hex)` with the key at `path`,
    read at the first signature, not before: a services object that never
    appends a block never makes a key."""
    def sign(index: int, block_hash: str) -> tuple[str, str]:
        key = load_or_create(path, chain=chain)
        return key.key_id, key.sign(block_statement(index, block_hash)).hex()
    return sign


def identity_source_for(path, chain):
    """The current key's public identity, with where it was made (`made_at_block`), for its announcement."""
    def source() -> dict:
        key = load_or_create(path, chain=chain)
        return {**key.identity(), "made_at_block": key.made_at_block}
    return source


def _took_over(made_at, otherwise: int, previous_start: int) -> int:
    """Where a key took over from the one before it: the block index the archive had reached when it was
    made, when that is known and falls inside the previous key's period, else `otherwise` (its announcement,
    or for a key still pending, the next block). A key file put back after another was made carries the
    point where it was first made, which falls before the previous period, so it falls back."""
    if isinstance(made_at, int) and not isinstance(made_at, bool) and previous_start <= made_at <= otherwise:
        return made_at
    return otherwise


def identity_document(key: InstanceKey, chain=None) -> dict:
    """What `GET /identity` serves (the operator's decision 1): the current
    key, and every key the chain has announced with its period, oldest first.
    A period is a range of blocks from the block that announced the key
    (`announced_in`) up to, not including, `until_block`. The current key's
    own period is open (`until_block: null`); any other ends where the next
    key took over: the archive's next block when that key was made, recorded
    in its key file and its announcement (`_took_over`), so a block appended
    unsigned afterwards (the new key unreadable, a rotation not yet announced)
    never widens it, and every block the period's archive held when the key
    was replaced stays inside it, a restore's included (W9's delta checks 1
    to 3). A key file put back after another was made reopens its own range
    (BACKLOG W9-B12). Blocks, not times, because a block's index only grows
    while a clock can be set back (a restore onto another machine);
    `active_from` is the announcing block's time and `active_to` the time of
    the block at `until_block`, null while no block is there yet, for a
    reader. A current key the chain has not announced yet (a first start, or
    a new key after a restore or a lost key file) is `pending`, with
    `announced_in: null` and `from_block` the next block: the next block
    appended announces it."""
    history = chain.announced_keys() if chain is not None else []
    latest = chain.latest() if chain is not None else None
    next_block = latest.index + 1 if latest is not None else 0
    pending = not history or history[-1]["key_id"] != key.key_id
    keys = []
    for n, a in enumerate(history):
        nxt = history[n + 1] if n + 1 < len(history) else None
        if nxt is not None:
            until = _took_over(nxt.get("made_at_block"), nxt["announced_in"], a["announced_in"])
        elif pending:
            until = _took_over(key.made_at_block, next_block, a["announced_in"])
        else:
            until = None                            # the current key's own period
        keys.append({"key_id": a["key_id"], "public_key": a["public_key"], "algorithm": a["algorithm"],
                     "reason": a["reason"], "previous_key_id": a["previous_key_id"],
                     "announced_in": a["announced_in"], "until_block": until,
                     "active_from": a["active_from"],
                     "active_to": chain.block_time(until) if until is not None else None})
    if pending:
        keys.append({"key_id": key.key_id, "public_key": key.public_key.hex(), "algorithm": ALGORITHM,
                     "reason": "rotation" if keys else "first",
                     "previous_key_id": keys[-1]["key_id"] if keys else None,
                     "announced_in": None, "until_block": None, "active_from": None, "active_to": None,
                     "pending": True, "from_block": next_block})
    return {**key.identity(), "keys": keys}
