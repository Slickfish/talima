"""NexusArchiveChain (arch §7.4, Law VIII) — append-only local hash chain.

Each archive block contains a fingerprint dict + a snapshot file's SHA-256
+ a link to the previous block's hash. Tampering with any historical BLOCK
breaks all subsequent hashes. verify_chain() walks the entire chain in O(n)
and returns the exact block where tampering occurred, if any.

WHAT IS VERIFIED, AND WHAT IS NOT. `verify_chain()` recomputes block hashes
and link equality from the ROWS in archive.db — it never re-reads a snapshot
file. `_hash_file` runs once, at `append()`. So the chain proves what the
snapshot's hash WAS at seal time; it does not prove the file on disk still
matches. A rewritten or overwritten snapshot leaves `verify_chain()` clean —
evidenced on a real store, where block 0's sealed hash and the file's current
hash differ while the chain verifies valid. `audit_snapshots()` is the check
that re-reads the files, kept OUT of `verify_chain()` deliberately: a
filesystem observation is a present-tense fact that changes when a file is
restored, and folding it into the verdict would conflate "the log was
tampered with" with "a snapshot was tampered with" — different attacks,
different responses. This paragraph replaced wording that read as though the
file were covered; a docstring asserting more than the code checks is the
same defect as the SSE contract's old "all 19 events" claim.

Arch §7.4 explicitly chose hash chain over a blockchain node — single-user
local system has no adversarial parties, so consensus is unnecessary. The
optional public anchor (OpenTimestamps / Bitcoin) lives in arch §11; this
module ships only the local chain.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from backend.memory import merkle
from backend.store import Database, Row, StoreTarget, connect as store_connect

DEFAULT_ARCHIVE_DB = Path("data/archive.db")
GENESIS_HASH       = "0" * 64
logger = logging.getLogger(__name__)

# W9 (run/specs/W9.md D7) — a time-stamping authority's receipt for a block's
# hash, one row per block, appended when the authority answers and never
# changed: the block's own row and hash are untouched.
BLOCK_ANCHORS_DDL = """
CREATE TABLE IF NOT EXISTS block_anchors (
    block_index  INTEGER PRIMARY KEY,
    method       TEXT    NOT NULL,
    authority    TEXT    NOT NULL,
    token        TEXT    NOT NULL,
    anchored_at  REAL    NOT NULL
);
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS archive_blocks (
    idx              INTEGER PRIMARY KEY,
    timestamp        REAL    NOT NULL,
    fingerprint_json TEXT    NOT NULL,
    snapshot_path    TEXT    NOT NULL,
    snapshot_hash    TEXT    NOT NULL,
    previous_hash    TEXT    NOT NULL,
    block_hash       TEXT    NOT NULL UNIQUE,
    -- S.2b: "snapshot" | "governance". A governance block seals lineage and
    -- recalibration records promptly and carries no snapshot; the periodic
    -- structural report keeps its own cadence. Defaulted so an archive.db
    -- written before S.2b reads back as all-snapshot, which it was.
    kind             TEXT    NOT NULL DEFAULT 'snapshot'
    -- W8's three columns (layout, roots_json, fingerprint_hash) are added by
    -- the migration below on every store, a fresh one included, so a fresh
    -- archive and one upgraded from S.2b on read them in one order (the store
    -- copy compares them; an archive older than S.2b reads `snapshot_missing`
    -- before `kind`: BACKLOG W8-B9).
);
CREATE INDEX IF NOT EXISTS idx_archive_ts ON archive_blocks(timestamp);
"""


def fingerprint_hash(fingerprint: dict) -> str:
    """W8 — SHA-256 of a fingerprint's canonical JSON (sorted keys, compact)."""
    return hashlib.sha256(json.dumps(fingerprint, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


# W8 — which seal a root is of, by the name a block's header gives it.
SEAL_ROOTS = (("lineage_seal", "lineage"), ("recalibration_seal", "governance"),
              ("binding_seal", "binding"),   # W8 (D6): records sealed before W8, bound
              ("commit_seal", "commit"),     # W9 (D8): held commitments, each a leaf
              ("action_seal", "action"))     # W10b (D6): governed actions, each record hash a leaf


def seal_leaves(seal: dict) -> list[bytes]:
    """The leaves a seal's root is over, in the seal's order: its `leaves`
    when it carries them (hex), else its sorted record hashes."""
    return [bytes.fromhex(x) for x in (seal.get("leaves") if "leaves" in seal else seal.get("record_hashes", []))]


def seal_roots(fingerprint: dict) -> dict:
    """The RFC 6962 root of each seal the fingerprint carries, by name, with
    the number of leaves it is over: `{"root": hex, "size": n}`. A Merkle path
    does not prove the tree's size (tests/test_w8_merkle.py M3b), so the size
    is sealed beside the root, in the header. {} when the fingerprint carries
    no seal."""
    fp = fingerprint or {}
    out = {}
    for key, name in SEAL_ROOTS:
        if isinstance(fp.get(key), dict):
            leaves = seal_leaves(fp[key])
            out[name] = {"root": merkle.root(leaves).hex(), "size": len(leaves)}
    return out


def derived_roots(block: "ArchiveBlock") -> dict:
    """A block's roots: a layout-2 block's stored ones; a layout-1 block's
    (every block before W8) computed from the lists it sealed, never stored
    back. A proof against a layout-1 block carries that list, because its
    hash covers the list and not a root."""
    return dict(block.roots or {}) if block.layout != 1 else seal_roots(block.fingerprint)


@dataclass(frozen=True)
class ArchiveBlock:
    index:           int
    timestamp:       float
    fingerprint:     dict
    snapshot_path:   str
    snapshot_hash:   str
    previous_hash:   str
    block_hash:      str = ""
    # P1/F-16 — chain honesty: when the snapshot file was absent at seal time,
    # the block says so instead of silently hashing the path string. The
    # snapshot_hash then covers the *fact of absence* (deterministic path hash).
    snapshot_missing: bool = False
    # S.2b — what this block is FOR. See SCHEMA_SQL.
    kind: str = "snapshot"
    # W8 — the hash's layout (SCHEMA_SQL), and a layout-2 block's roots: the
    # RFC 6962 root of each seal's leaves with its size, keyed `lineage` and
    # `governance` (`seal_roots`); {} when the block seals nothing.
    layout: int = 1
    roots: dict | None = None
    # W8 — a layout-2 block's header value for its fingerprint, as stored, so a
    # fingerprint edited after the seal is named apart from a header edit.
    fingerprint_digest: str | None = None
    # W9 (run/specs/W9.md D4) — the instance's Ed25519 signature over this
    # block's statement (`instance_key.block_statement`), made when it was
    # appended, and the id of the key that made it. Beside the block, never in
    # its hash: every earlier block and the chain verify unchanged. None for a
    # block appended before W9, or one whose signing failed (counted).
    instance_signature: str | None = None
    instance_key_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "index":            self.index,
            "timestamp":        self.timestamp,
            "fingerprint":      self.fingerprint,
            "snapshot_path":    self.snapshot_path,
            "snapshot_hash":    self.snapshot_hash,
            "previous_hash":    self.previous_hash,
            "block_hash":       self.block_hash,
            "snapshot_missing": self.snapshot_missing,
            "kind":             self.kind,
        }

    @property
    def fingerprint_json(self) -> str:
        """Serialised fingerprint payload — Fix B's drift assessment loads the
        raw string from this property and json.loads() it back to a dict."""
        return json.dumps(self.fingerprint, sort_keys=True)

    def header(self) -> dict:
        """W8 — a layout-2 block's hashed header: everything but the fingerprint
        itself, which it carries as `fingerprint_hash`, and the roots in the
        clear, so a block's hash is checked from its header and a Merkle path,
        never from the whole list it seals."""
        body = {
            "layout":           self.layout,
            "index":            self.index,
            "timestamp":        self.timestamp,
            "fingerprint_hash": self.fingerprint_digest or fingerprint_hash(self.fingerprint),
            "roots":            dict(self.roots or {}),
            "snapshot_path":    self.snapshot_path,
            "snapshot_hash":    self.snapshot_hash,
            "previous_hash":    self.previous_hash,
        }
        if self.snapshot_missing:
            body["snapshot_missing"] = True
        if self.kind != "snapshot":
            body["kind"] = self.kind
        return body

    def compute_hash(self) -> str:
        """Deterministic block hash over every field except block_hash itself.
        Layout 2 (W8) hashes its header; layout 1, every block before W8, is
        hashed exactly as it always was."""
        if self.layout != 1:
            encoded = json.dumps(self.header(), sort_keys=True, separators=(",", ":")).encode("utf-8")
            return hashlib.sha256(encoded).hexdigest()
        body = {
            "index":         self.index,
            "timestamp":     self.timestamp,
            "fingerprint":   self.fingerprint,
            "snapshot_path": self.snapshot_path,
            "snapshot_hash": self.snapshot_hash,
            "previous_hash": self.previous_hash,
        }
        # F-16: sealed into the hash only when True, so blocks appended before
        # this field existed recompute to their stored hashes unchanged.
        if self.snapshot_missing:
            body["snapshot_missing"] = True
        # S.2b, same rule and deliberately the same rule: sealed into the hash
        # only when it is not the default, so every block appended before this
        # field existed still recomputes to its stored hash. A new field that
        # silently invalidated the historical chain would be the one change a
        # tamper-evident log must never make to itself.
        if self.kind != "snapshot":
            body["kind"] = self.kind
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class ChainVerificationResult:
    valid:      bool
    n_blocks:   int
    broken_at:  int | None = None
    reason:     str = ""
    # F-16 — indices of blocks sealed while their snapshot file was absent.
    # The chain still verifies; the gap in snapshot coverage is surfaced.
    missing_snapshots: tuple = ()

    def to_dict(self) -> dict:
        return {
            "valid":             self.valid,
            "n_blocks":          self.n_blocks,
            "broken_at":         self.broken_at,
            "reason":            self.reason,
            "missing_snapshots": list(self.missing_snapshots),
        }


class NexusArchiveChain:
    GENESIS_HASH = GENESIS_HASH

    def __init__(self, db_path: "Path | str | StoreTarget" = DEFAULT_ARCHIVE_DB):
        # W3b — a path is SQLite exactly as before; a StoreTarget names the
        # backend (`backend.store`).
        # W9 (D4) — set by `create_services`: `signer(index, block_hash)` returns
        # `(key_id, signature_hex)`, `identity_source()` the current key's public
        # identity. Absent (a script's chain, a test's), blocks are appended
        # unsigned and announce nothing, as before W9.
        self.signer = None
        self.identity_source = None
        self.unsigned_appends = 0          # signing attempted and failed, since this object was made
        self.last_signing_error: str | None = None
        self._announced: list | None = None    # announced_keys(), read once, then kept by append
        self.target = StoreTarget.of(db_path)
        self.db_path = self.target.path
        self._conn: Database | None = None
        self.init()

    # ---------- lifecycle ----------

    def init(self) -> None:
        """Create the archive db file + schema. Idempotent."""
        self._conn = store_connect(self.target)
        self._conn.executescript(SCHEMA_SQL)
        self._conn.executescript(BLOCK_ANCHORS_DDL)     # W9 (D7): beside the blocks, never in them
        # P1/F-16 — additive, re-runnable migration: older archive DBs lack the
        # snapshot_missing column; add it with a default so existing rows keep
        # verifying (compute_hash omits the field when False).
        cols = {c.name for c in self._conn.columns("archive_blocks")}
        if "snapshot_missing" not in cols:
            self._conn.execute(
                "ALTER TABLE archive_blocks "
                "ADD COLUMN snapshot_missing INTEGER NOT NULL DEFAULT 0")
        # S.2b — same shape. An archive.db written before governance blocks
        # existed contained only snapshots, so that is what the default says.
        if "kind" not in cols:
            self._conn.execute(
                "ALTER TABLE archive_blocks "
                "ADD COLUMN kind TEXT NOT NULL DEFAULT 'snapshot'")
        # W8 — same shape, on a fresh store too: 1 = the hash is over the whole
        # fingerprint (every block before W8); 2 = over a header carrying the
        # fingerprint's hash and the seals' Merkle roots. Defaulted so an
        # earlier archive reads as layout 1 with no roots.
        if "layout" not in cols:
            self._conn.execute(
                "ALTER TABLE archive_blocks ADD COLUMN layout INTEGER NOT NULL DEFAULT 1")
        if "roots_json" not in cols:
            self._conn.execute("ALTER TABLE archive_blocks ADD COLUMN roots_json TEXT")
        if "fingerprint_hash" not in cols:
            self._conn.execute("ALTER TABLE archive_blocks ADD COLUMN fingerprint_hash TEXT")
        # W9 (D4) — same shape: the signature beside the block, outside its hash.
        if "instance_signature" not in cols:
            self._conn.execute("ALTER TABLE archive_blocks ADD COLUMN instance_signature TEXT")
        if "instance_key_id" not in cols:
            self._conn.execute("ALTER TABLE archive_blocks ADD COLUMN instance_key_id TEXT")
        self._conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> Database:
        if self._conn is None:
            self.init()
        return self._conn   # type: ignore[return-value]

    # ---------- write ----------

    def append(
        self,
        fingerprint: dict,
        snapshot_path: str | Path,
        *,
        kind: str = "snapshot",
    ) -> ArchiveBlock:
        snapshot_path = str(snapshot_path)
        # P1/F-16 — record honestly when the snapshot is absent at seal time;
        # snapshot_hash then covers the fact of absence (deterministic path
        # hash) and the flag is sealed into the block hash.
        snapshot_missing = not Path(snapshot_path).is_file()
        snapshot_hash = self._hash_file(snapshot_path)
        previous = self._latest_block()
        announced = self._announce(fingerprint)     # W9 — a key the chain has not announced last
        unannounced = announced is None             # its standing unread: the block is appended, never signed
        fingerprint = fingerprint if unannounced else announced
        roots = seal_roots(fingerprint)          # W8 — every block appended now is layout 2

        candidate = ArchiveBlock(
            index         = (previous.index + 1) if previous else 0,
            timestamp     = time.time(),
            fingerprint   = fingerprint,
            snapshot_path = snapshot_path,
            snapshot_hash = snapshot_hash,
            previous_hash = previous.block_hash if previous else GENESIS_HASH,
            snapshot_missing = snapshot_missing,
            kind          = kind,
            layout        = 2,
            roots         = roots,
            fingerprint_digest = fingerprint_hash(fingerprint),
        )
        block_hash = candidate.compute_hash()
        key_id = signature = None
        if self.signer is not None and unannounced:
            # A key signs no block that does not follow its announcement, so a
            # pending rotation's boundary (`identity_document`) cannot move.
            self.unsigned_appends += 1
            logger.warning("archive block %d appended unsigned: %s", candidate.index, self.last_signing_error)
        elif self.signer is not None:
            try:
                key_id, signature = self.signer(candidate.index, block_hash)
            except Exception as e:                      # the chain's hashes do not rest on the key
                self.unsigned_appends += 1
                self.last_signing_error = f"{type(e).__name__}: {e}"
                logger.warning("archive block %d appended unsigned: %s", candidate.index, self.last_signing_error)
        block = ArchiveBlock(
            index         = candidate.index,
            timestamp     = candidate.timestamp,
            fingerprint   = candidate.fingerprint,
            snapshot_path = candidate.snapshot_path,
            snapshot_hash = candidate.snapshot_hash,
            previous_hash = candidate.previous_hash,
            block_hash    = block_hash,
            snapshot_missing = candidate.snapshot_missing,
            kind          = candidate.kind,
            layout        = candidate.layout,
            roots         = candidate.roots,
            fingerprint_digest = candidate.fingerprint_digest,
            instance_signature = signature,
            instance_key_id = key_id,
        )
        self.conn.execute(
            "INSERT INTO archive_blocks "
            "(idx, timestamp, fingerprint_json, snapshot_path, "
            " snapshot_hash, previous_hash, block_hash, snapshot_missing, kind, layout, roots_json,"
            " fingerprint_hash, instance_signature, instance_key_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (block.index, block.timestamp,
             json.dumps(block.fingerprint, sort_keys=True),
             block.snapshot_path, block.snapshot_hash,
             block.previous_hash, block.block_hash,
             int(block.snapshot_missing), block.kind, block.layout,
             json.dumps(block.roots, sort_keys=True), block.fingerprint_digest,
             block.instance_signature, block.instance_key_id),
        )
        self.conn.commit()
        if self._announced is not None and isinstance(block.fingerprint.get("identity_seal"), dict):
            self._announced.append({**block.fingerprint["identity_seal"], "announced_in": block.index,
                                    "active_from": float(block.timestamp)})
        return block

    def _announce(self, fingerprint: dict) -> dict | None:
        """W9 (the operator's decision 1) — the fingerprint with an
        `identity_seal` when the current key is not the last key the chain
        announced: the first key (`first`), or a new one after a restore that
        did not carry the private key (`rotation`). The announcing block is
        the first the key signs in its period, so the period opens at that
        block's time. Never raises: None when the key or the history cannot
        be read, and `append` then appends the block unsigned and counts it."""
        if self.identity_source is None:
            return fingerprint
        try:
            current = self.identity_source()
        except Exception as e:
            self.last_signing_error = f"{type(e).__name__}: {e}"
            return None
        try:
            history = self.announced_keys()
        except Exception as e:
            self.last_signing_error = f"{type(e).__name__}: {e}"
            return None
        last = history[-1] if history else None
        if last is not None and last["key_id"] == current["key_id"]:
            return fingerprint
        seal = {"key_id": current["key_id"], "public_key": current["public_key"],
                "algorithm": current["algorithm"], "reason": "rotation" if last else "first",
                "previous_key_id": last["key_id"] if last else None}
        made = current.get("made_at_block")
        if isinstance(made, int) and not isinstance(made, bool):
            seal["made_at_block"] = made          # where the instance moved to this key (delta check 3)
        return {**fingerprint, "identity_seal": seal}

    def unanchored_signed(self, *, limit: int) -> list:
        """W9 (D7) — signed blocks no receipt covers yet, oldest first."""
        rows = self.conn.execute(
            "SELECT b.* FROM archive_blocks b LEFT JOIN block_anchors a ON a.block_index = b.idx "
            "WHERE b.instance_signature IS NOT NULL AND a.block_index IS NULL ORDER BY b.idx").fetchall()
        return [self._row_to_block(r) for r in rows[:limit]]

    def store_anchor(self, block_index: int, *, method: str, authority: str, token: str, at: float) -> None:
        self.conn.execute("INSERT INTO block_anchors (block_index, method, authority, token, anchored_at) "
                          "VALUES (?, ?, ?, ?, ?)", (block_index, method, authority, token, at))
        self.conn.commit()

    def block_time(self, index: int) -> "float | None":
        row = self.conn.execute("SELECT timestamp FROM archive_blocks WHERE idx = ?", (index,)).fetchone()
        return None if row is None else float(row["timestamp"])

    def anchor_for(self, block_index: int) -> "dict | None":
        row = self.conn.execute("SELECT * FROM block_anchors WHERE block_index = ?", (block_index,)).fetchone()
        return None if row is None else {"block_index": int(row["block_index"]), "method": row["method"],
                                          "authority": row["authority"], "token": row["token"],
                                          "anchored_at": float(row["anchored_at"])}

    def anchor_counts(self) -> dict:
        signed = self.conn.execute(
            "SELECT COUNT(*) AS n FROM archive_blocks WHERE instance_signature IS NOT NULL").fetchone()["n"]
        anchored = self.conn.execute("SELECT COUNT(*) AS n FROM block_anchors").fetchone()["n"]
        return {"signed_blocks": int(signed), "anchored_blocks": int(anchored)}

    def announced_keys(self) -> list[dict]:
        """W9 — every `identity_seal` in block order, each with the block that
        announced it and that block's time (the start of the key's period).
        Read from the store once (`LOWER(...)`: the dialect's one measured
        LIKE, and the key is lower case), then kept as this object appends."""
        if self._announced is None:
            rows = self.conn.execute(
                "SELECT idx, timestamp, fingerprint_json FROM archive_blocks "
                "WHERE LOWER(fingerprint_json) LIKE ? ORDER BY idx", ('%"identity_seal"%',)).fetchall()
            found = []
            for r in rows:
                seal = (json.loads(r["fingerprint_json"]) or {}).get("identity_seal")
                if isinstance(seal, dict):
                    found.append({**seal, "announced_in": int(r["idx"]), "active_from": float(r["timestamp"])})
            self._announced = found
        return [dict(a) for a in self._announced]

    # ---------- read ----------

    def count(self) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) AS n FROM archive_blocks"
        ).fetchone()["n"])

    def latest(self) -> ArchiveBlock | None:
        return self._latest_block()

    def all_blocks(self) -> list[ArchiveBlock]:
        rows = self.conn.execute(
            "SELECT * FROM archive_blocks ORDER BY idx ASC"
        ).fetchall()
        return [self._row_to_block(r) for r in rows]

    def latest_snapshot_block(self) -> ArchiveBlock | None:
        """Most recent block that carries a fingerprint (S.2b).

        Drift compares consecutive FINGERPRINTS. Once governance blocks
        interleave, `load_recent_blocks(n=1)` returns whichever block was
        appended last, and a governance block's payload has no fingerprint
        fields at all — `CortexFingerprint.from_dict` is key-tolerant, so the
        comparison would silently run against zeros and report drift that did
        not happen. Measured consequence, not a hypothetical: a spurious
        `cortex_drift` alert on every cycle after any recalibration.
        """
        row = self.conn.execute(
            "SELECT * FROM archive_blocks WHERE kind = 'snapshot' "
            "ORDER BY idx DESC LIMIT 1").fetchone()
        return self._row_to_block(row) if row else None

    def get_block(self, index: int) -> ArchiveBlock | None:
        """One block by its index, or None.

        H.1 — `verify_sealed_lineage()` resolves `sealed_block -> block` for
        every record it checks. Without this it would scan `all_blocks()` per
        record, which is O(records x blocks) for a lookup SQLite can do by
        primary key. Cheap either way at monthly cadence, but a scan standing
        in for a lookup reads as if no lookup were possible.
        """
        row = self.conn.execute(
            "SELECT * FROM archive_blocks WHERE idx = ?", (index,)
        ).fetchone()
        return self._row_to_block(row) if row else None

    def load_recent_blocks(self, n: int = 1) -> list[ArchiveBlock]:
        """Return the N most-recent blocks in chronological (asc) order.

        Used by Fix B's drift policy and /cortex/drift endpoint to compare
        the latest two fingerprints without scanning the whole chain.
        """
        if n <= 0:
            return []
        rows = self.conn.execute(
            "SELECT * FROM archive_blocks ORDER BY idx DESC LIMIT ?", (n,),
        ).fetchall()
        return [self._row_to_block(r) for r in reversed(rows)]

    # ---------- verification ----------

    def verify_chain(self) -> ChainVerificationResult:
        """Walk the chain, recompute every hash, verify every link.

        420 blocks (35-year archive at monthly snapshots) verifies in <100ms.
        """
        blocks = self.all_blocks()
        # S.2b — a governance block has no snapshot BY DESIGN, so counting it
        # here would flood F-16's signal with intended absences and destroy
        # the one thing that list is for: naming the snapshot blocks whose
        # file was missing when they were sealed.
        missing = tuple(b.index for b in blocks
                        if b.snapshot_missing and b.kind == "snapshot")
        for i, block in enumerate(blocks):
            if block.layout != 1:
                # W8 — a layout-2 block's hash is over its header, so the two
                # things the header carries for the fingerprint are checked
                # against it first, and named apart from a header edit.
                if block.fingerprint_digest != fingerprint_hash(block.fingerprint):
                    return ChainVerificationResult(
                        valid=False, n_blocks=len(blocks), broken_at=block.index,
                        reason="fingerprint_mismatch", missing_snapshots=missing,
                    )
                if dict(block.roots or {}) != seal_roots(block.fingerprint):
                    return ChainVerificationResult(
                        valid=False, n_blocks=len(blocks), broken_at=block.index,
                        reason="roots_mismatch", missing_snapshots=missing,
                    )
            if block.compute_hash() != block.block_hash:
                return ChainVerificationResult(
                    valid=False, n_blocks=len(blocks), broken_at=block.index,
                    reason="block_hash_mismatch", missing_snapshots=missing,
                )
            expected_prev = blocks[i - 1].block_hash if i > 0 else GENESIS_HASH
            if block.previous_hash != expected_prev:
                return ChainVerificationResult(
                    valid=False, n_blocks=len(blocks), broken_at=block.index,
                    reason="chain_link_broken", missing_snapshots=missing,
                )
        return ChainVerificationResult(
            valid=True, n_blocks=len(blocks), missing_snapshots=missing,
        )

    # ---------- snapshot audit (additive — never part of the verdict) ------

    def audit_snapshots(self) -> dict:
        """Compare each block's sealed snapshot hash against the file on disk.

        THE CHECK `verify_chain()` DELIBERATELY IS NOT. The verdict is a pure
        function of the rows — a claim about the immutable log — while this is
        a present-tense filesystem observation that changes when a file is
        deleted, restored, or planted. Folding it into the verdict would
        conflate "the log was tampered with" with "a snapshot was tampered
        with": different attacks, different responses, the same argument
        `verify_sealed_lineage` makes for keeping row/seal/chain apart. An
        AST guard in tests/test_snapshot_tamper_evidence.py holds the
        separation structurally.

        Six states, kept distinct because collapsing any pair destroys a
        signal:

          intact            file present, hash matches the seal
          altered           file present, hash DIFFERS — includes the earlier
                            block of a same-second overwrite (pre-collision-
                            guard), whose sealed content is unrecoverable
          missing           file existed at seal time and is gone now
          missing_at_seal   absent when sealed, still absent (F-16's state) —
                            consistent with the record, not an alarm, and not
                            "intact" because no file was checked
          unexpected_file   sealed as absent, a file exists there NOW —
                            planted where the chain recorded absence
          absent_by_design  governance blocks carry no snapshot (S.2b)

        Disk I/O proportional to chain length, which is why this is NOT
        computed on the `/chain/status` poll the UI hits on a timer — it is
        opt-in there (`?audit=true`). Read-only: hashes are recomputed to
        compare, never to replace a stored value.
        """
        blocks = self.all_blocks()
        rows: list[dict] = []
        altered: list[int] = []
        missing: list[int] = []
        missing_at_seal: list[int] = []
        unexpected: list[int] = []
        intact = absent_by_design = 0

        for b in blocks:
            live_hash: str | None = None
            if b.kind != "snapshot":
                state = "absent_by_design"
                absent_by_design += 1
            else:
                on_disk = Path(b.snapshot_path).is_file() if b.snapshot_path \
                    else False
                if b.snapshot_missing:
                    if on_disk:
                        state = "unexpected_file"
                        unexpected.append(b.index)
                        live_hash = self._hash_file(b.snapshot_path)
                    else:
                        state = "missing_at_seal"
                        missing_at_seal.append(b.index)
                elif not on_disk:
                    state = "missing"
                    missing.append(b.index)
                else:
                    live_hash = self._hash_file(b.snapshot_path)
                    if live_hash == b.snapshot_hash:
                        state = "intact"
                        intact += 1
                    else:
                        state = "altered"
                        altered.append(b.index)
            rows.append({
                "index":         b.index,
                "kind":          b.kind,
                "state":         state,
                "snapshot_path": b.snapshot_path,
                # Both hashes on an altered block, so an operator can search
                # backups for the sealed content rather than only learning it
                # is gone.
                "sealed_hash":   b.snapshot_hash,
                "live_hash":     live_hash,
            })

        return {
            "n_blocks":         len(blocks),
            "intact":           intact,
            "absent_by_design": absent_by_design,
            "altered":          altered,
            "missing":          missing,
            "missing_at_seal":  missing_at_seal,
            "unexpected_file":  unexpected,
            "blocks":           rows,
        }

    # ---------- helpers ----------

    def _latest_block(self) -> ArchiveBlock | None:
        row = self.conn.execute(
            "SELECT * FROM archive_blocks ORDER BY idx DESC LIMIT 1"
        ).fetchone()
        return self._row_to_block(row) if row else None

    @staticmethod
    def _row_to_block(row: Row) -> ArchiveBlock:
        return ArchiveBlock(
            index         = row["idx"],
            timestamp     = row["timestamp"],
            fingerprint   = json.loads(row["fingerprint_json"]),
            snapshot_path = row["snapshot_path"],
            snapshot_hash = row["snapshot_hash"],
            previous_hash = row["previous_hash"],
            block_hash    = row["block_hash"],
            snapshot_missing = bool(row["snapshot_missing"]),
            kind          = (row["kind"] if "kind" in row.keys()
                             and row["kind"] else "snapshot"),
            layout        = (int(row["layout"]) if "layout" in row.keys() and row["layout"] else 1),
            roots         = (json.loads(row["roots_json"]) if "roots_json" in row.keys() and row["roots_json"]
                             else None),
            fingerprint_digest = (row["fingerprint_hash"] if "fingerprint_hash" in row.keys() else None),
            instance_signature = (row["instance_signature"] if "instance_signature" in row.keys() else None),
            instance_key_id = (row["instance_key_id"] if "instance_key_id" in row.keys() else None),
        )

    @staticmethod
    def _hash_file(path: str) -> str:
        """SHA-256 of a snapshot file. If the file doesn't exist (e.g. tests),
        we hash the path string itself so the chain still has a deterministic
        link — verification still works."""
        p = Path(path)
        h = hashlib.sha256()
        if p.is_file():
            with p.open("rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
        else:
            h.update(path.encode("utf-8"))
        return h.hexdigest()
