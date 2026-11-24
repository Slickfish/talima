"""The Postgres spelling of the SQL the stores hold, enumerated (W3b, D4).

The store modules write SQLite's SQL. On Postgres every statement passes
through `translate()` and every DDL script through `translate_ddl()`; a
construct the translator does not know is refused by name
(`DialectError`), never passed through, so a new SQLite spelling fails the
suite rather than a tester. The rewrites are the ones the establish's
construct table found (`run/specs/W3b.md` S1 item 1), each measured:

- `?` to `%s` (psycopg's placeholder) outside string literals; a literal
  `%` doubled only when parameters are passed (psycopg leaves a parameterless
  query's text alone and would otherwise read `%%` as two characters:
  measured);
- `INSERT OR IGNORE INTO` to `INSERT INTO … ON CONFLICT DO NOTHING`
  (narrower: SQLite's `OR IGNORE` also swallows NOT NULL and CHECK
  violations, declared in D15);
- two-or-more-argument `MAX`/`MIN` to `GREATEST`/`LEAST` (scalars on SQLite,
  aggregates only on Postgres; every operand in the backend is `NOT NULL`,
  so the NULL rules do not diverge); a one-argument `MAX` is the aggregate
  on both and is left alone;
- `LOWER(` to `nexus_lower(`, a function that lowers ASCII and nothing
  else, because SQLite's `LOWER` does exactly that (measured: `LOWER('É')`
  is `É`) while Postgres's `lower()` folds whatever the column's ctype
  says; a `LIKE` whose left operand is not that call is refused, because
  SQLite's `LIKE` is ASCII-case-insensitive and Postgres's is not, and the
  one shape whose parity is measured is both sides lowered;
- every `LIKE` given `ESCAPE ''` (W3b part 4): Postgres's LIKE escapes with
  the backslash and SQLite's has no escape character, so a backslash in a
  pattern matched different rows (measured); a `LIKE` whose pattern is not
  a placeholder or a string literal is refused;
- `IFNULL(` to `COALESCE(`; `CAST(x AS INTEGER)` to `BIGINT` and `AS REAL` to
  `DOUBLE PRECISION` (none in the backend today; harmless);
- refused by name: `PRAGMA` and `sqlite_master` (the interface's
  `columns()`, `has_table()` and `describe()` are the twins), `GROUP_CONCAT`,
  `RANDOM()`, `typeof()`, the date functions, `GLOB`, `REPLACE INTO` and
  every `INSERT OR <clause>` but `IGNORE`.

DDL: `INTEGER` to `BIGINT`, `REAL` to `DOUBLE PRECISION` (never `real`, a
float4), `TEXT` to `TEXT COLLATE "C"` (SQLite's `BINARY` collation is the
UTF-8 byte order; Postgres's default under the deployment's `en_US.UTF-8`
orders `a` before `A` and ignores `_` at the first level: measured), `BLOB`
to `BYTEA`; `REFERENCES` dropped (SQLite does not enforce them,
`foreign_keys=0` measured on every store connection: a backend that
refused what the other accepts would be a declared difference for
nothing). `INTEGER PRIMARY KEY AUTOINCREMENT` becomes `BIGINT PRIMARY KEY`
with a `BEFORE INSERT` trigger that assigns ids as SQLite's
`sqlite_sequence` assigns them (D3): a transactional `nexus_sequence` row
per table, bumped for every proposed row (consumed by an upsert hit, as
SQLite consumes), rolled back with the transaction (as SQLite's is),
moved past an explicit id (as SQLite's is), so a migrated store continues
exactly where it was. The two tables SQL orders by their implicit rowid
(`archival_events`, read by as-of; `hebbian_edges`, whose load order
seeds the prompt's adjacency: H2) get an explicit `rowid BIGINT` assigned
`max + 1` by a trigger, SQLite's rule for a table that never deletes.

Everything works on MASKED text: string literals ('...' with '' escapes),
quoted identifiers and comments are replaced by placeholders before any
rule runs and restored after, so a `?` inside a literal and a word inside a
comment are never rewritten. The one rule that reaches inside a literal is
the `%` doubling: psycopg's placeholder scan is textual and reads literals
too (`'a%b'` counts `%b` as a placeholder, `'gate:%'` is refused: measured,
W3b part 3), so with parameters every `%` is doubled, literals included. `tests/test_w3b_dialect.py` holds the
rules' tests on both backends and the derived guard that prepares every
SQL string the tracked backend holds against a Postgres schema.
"""
from __future__ import annotations

import re
from functools import lru_cache

#: Tables whose implicit SQLite rowid is read by SQL (ordered or relied on
#: for adjacency order), so Postgres carries an explicit ordinal (D3).
ROWID_TABLES = ("archival_events", "hebbian_edges", "work_unit_events")   # W6: the boundary ledger's order
SEQUENCE_TABLE = "nexus_sequence"
LOWER_FUNCTION = "nexus_lower"
#: The single-writer lock's holder and takeover log (W3b part 3, D8): the
#: twins of `<kb.db>.lock` and `<kb.db>.lock.log`, which SQLite keeps beside
#: the store, so `tables()` leaves them out as it leaves `nexus_sequence`.
LOCK_TABLE = "nexus_store_lock"
LOCK_LOG_TABLE = "nexus_store_lock_log"
SUPPORT_TABLES = (SEQUENCE_TABLE, LOCK_TABLE, LOCK_LOG_TABLE)

#: sqlite3's own rule for the statements that open an implicit transaction:
#: the first word of the statement.
DML_FIRST_WORDS = ("insert", "update", "delete", "replace")


class DialectError(ValueError):
    """A SQLite spelling with no Postgres twin here: refused by name."""


# ---------------------------------------------------------------------------
# masking
# ---------------------------------------------------------------------------

_MASK = "\x00{}\x00"
_MASK_RE = re.compile("\x00(\\d+)\x00")


def mask(sql: str) -> tuple[str, list[str]]:
    """Replace string literals and quoted identifiers by numbered
    placeholders and drop comments (`--` to the line's end, `/* */`)."""
    out: list[str] = []
    held: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'":
            j = i + 1
            while j < n:
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            held.append(sql[i:j + 1])
            out.append(_MASK.format(len(held) - 1))
            i = j + 1
        elif c == '"':
            j = sql.find('"', i + 1)
            j = n - 1 if j < 0 else j
            held.append(sql[i:j + 1])
            out.append(_MASK.format(len(held) - 1))
            i = j + 1
        elif sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j            # the newline stays
            out.append(" ")
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        else:
            out.append(c)
            i += 1
    return "".join(out), held


def unmask(text: str, held: list[str]) -> str:
    return _MASK_RE.sub(lambda m: held[int(m.group(1))], text)


def first_keyword(sql: str) -> str:
    """The statement's first word, lowercased, after whitespace, comments
    and opening parentheses."""
    masked, _ = mask(sql)
    m = re.match(r"\s*\(*\s*([A-Za-z]+)", masked)
    return m.group(1).lower() if m else ""


def is_dml(sql: str) -> bool:
    """Whether sqlite3 would open an implicit transaction for `sql`."""
    return first_keyword(sql) in DML_FIRST_WORDS


DDL_FIRST_WORDS = ("create", "alter", "drop")


def is_ddl(sql: str) -> bool:
    """A definition statement: translated by `translate_ddl`, never by
    `translate`, whichever method it arrives through."""
    return first_keyword(sql) in DDL_FIRST_WORDS


# ---------------------------------------------------------------------------
# statements
# ---------------------------------------------------------------------------

REFUSED: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"\bPRAGMA\b", re.I), "PRAGMA has no Postgres twin: use Database.columns(), has_table() or describe()"),
    (re.compile(r"\bsqlite_(master|sequence|schema)\b", re.I), "sqlite_master has no Postgres twin: use Database.has_table()"),
    (re.compile(r"\bGROUP_CONCAT\s*\(", re.I), "GROUP_CONCAT is not spelled here"),
    (re.compile(r"\bRANDOM\s*\(", re.I), "RANDOM() is not spelled here"),
    (re.compile(r"\btypeof\s*\(", re.I), "typeof() is not spelled here"),
    (re.compile(r"\b(strftime|julianday|datetime|unixepoch)\s*\(", re.I), "SQLite's date functions are not spelled here"),
    (re.compile(r"\bGLOB\b", re.I), "GLOB is not spelled here"),
    (re.compile(r"\bINSERT\s+OR\s+(REPLACE|ABORT|FAIL|ROLLBACK)\b", re.I), "INSERT OR <clause> other than IGNORE is not spelled here"),
    (re.compile(r"\bREPLACE\s+INTO\b", re.I), "REPLACE INTO is not spelled here"),
    # AVG returns numeric on Postgres and a float on SQLite; TOTAL is SQLite's
    # alone. Neither is in the backend; a SUM is allowed inside a CAST only
    # (`_check_sum`): SUM over an integer column is numeric on Postgres (a
    # Decimal in Python, which no JSON surface carries) and an integer on
    # SQLite, so the cast names the type on both.
    (re.compile(r"\bAVG\s*\(", re.I), "AVG's result type differs between the backends (numeric against float): not spelled here"),
    (re.compile(r"\bTOTAL\s*\(", re.I), "TOTAL is SQLite's alone: not spelled here"),
)

_SUM = re.compile(r"\bSUM\s*\(", re.I)
_CAST = re.compile(r"\bCAST\s*\(", re.I)


def _check_sum(masked: str) -> None:
    """Every `SUM(` must lie inside a `CAST(… AS INTEGER|REAL)` span: SQLite's
    SUM over an integer column is an integer and Postgres's is numeric, so
    the statement says which it means, on both."""
    sums = [m.start() for m in _SUM.finditer(masked)]
    if not sums:
        return
    spans = []
    for m in _CAST.finditer(masked):
        open_idx = m.end() - 1
        spans.append((open_idx, _matching_paren(masked, open_idx)))
    for s in sums:
        if not any(a < s < b for a, b in spans):
            raise DialectError("SUM outside a CAST has no one type on both backends (SQLite: integer; "
                               "Postgres: numeric): write CAST(SUM(…) AS INTEGER) or AS REAL")

_OR_IGNORE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.I)
_RETURNING = re.compile(r"\bRETURNING\b", re.I)
_ON_CONFLICT = re.compile(r"\bON\s+CONFLICT\b", re.I)
_LOWER = re.compile(r"\bLOWER\s*\(", re.I)
_IFNULL = re.compile(r"\bIFNULL\s*\(", re.I)
_CAST_INT = re.compile(r"\bAS\s+INTEGER\s*\)", re.I)
_CAST_REAL = re.compile(r"\bAS\s+REAL\s*\)", re.I)
_MAXMIN = re.compile(r"\b(MAX|MIN)\s*\(", re.I)
_LIKE = re.compile(r"\bLIKE\b", re.I)


def _matching_paren(text: str, open_idx: int) -> int:
    depth = 0
    for k in range(open_idx, len(text)):
        if text[k] == "(":
            depth += 1
        elif text[k] == ")":
            depth -= 1
            if depth == 0:
                return k
    raise DialectError("unbalanced parentheses")


def _top_level_commas(text: str) -> int:
    depth, commas = 0, 0
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            commas += 1
    return commas


def _rewrite_scalar_max_min(masked: str) -> str:
    out, pos = [], 0
    for m in _MAXMIN.finditer(masked):
        if m.start() < pos:
            continue
        open_idx = m.end() - 1
        close_idx = _matching_paren(masked, open_idx)
        if _top_level_commas(masked[open_idx + 1:close_idx]) >= 1:
            out.append(masked[pos:m.start()])
            out.append(("GREATEST" if m.group(1).upper() == "MAX" else "LEAST") + "(")
            pos = open_idx + 1
    out.append(masked[pos:])
    return "".join(out)


def _check_like(masked: str) -> None:
    for m in _LIKE.finditer(masked):
        k = m.start() - 1
        while k >= 0 and masked[k].isspace():
            k -= 1
        ok = False
        if k >= 0 and masked[k] == ")":
            depth, j = 0, k
            while j >= 0:
                if masked[j] == ")":
                    depth += 1
                elif masked[j] == "(":
                    depth -= 1
                    if depth == 0:
                        break
                j -= 1
            name = re.search(r"([A-Za-z_][A-Za-z0-9_]*)\s*\Z", masked[:j])
            ok = bool(name) and name.group(1).lower() == LOWER_FUNCTION
        if not ok:
            raise DialectError("a LIKE whose left operand is not LOWER(...) has no measured Postgres twin "
                               "(SQLite's LIKE is ASCII-case-insensitive; Postgres's is not)")


_LIKE_RHS = re.compile(r"\bLIKE\s+(\?|\x00(\d+)\x00)", re.I)
_ESCAPE_AFTER = re.compile(r"\s*ESCAPE\b", re.I)


def _like_without_escape(masked: str, held: list[str]) -> str:
    """Every `LIKE` gets `ESCAPE ''`. SQLite's LIKE has no escape character
    unless one is named, and Postgres's is the backslash, so a backslash in
    a pattern matched different rows on Postgres: `%a\\b%` found the `ab`
    row and not the `a\\b` one, and a term ending in a backslash matched
    nothing (measured, W3b part 4). With `ESCAPE ''` Postgres reads a
    backslash as itself, as SQLite does (measured: the same rows). The right
    operand must be a placeholder or a string literal, the one shape whose
    parity is measured; anything else is refused by name. A statement that
    names its own ESCAPE keeps it."""
    out, pos = [], 0
    for m in _LIKE.finditer(masked):
        rhs = _LIKE_RHS.match(masked, m.start())
        if rhs is None or (rhs.group(2) is not None and not held[int(rhs.group(2))].startswith("'")):
            raise DialectError("a LIKE whose right operand is not a placeholder or a string literal has no "
                               "measured Postgres twin (the escape rule needs the pattern to be one of the two)")
        end = rhs.end()
        out.append(masked[pos:end])
        if not _ESCAPE_AFTER.match(masked, end):
            out.append(" ESCAPE ''")
        pos = end
    out.append(masked[pos:])
    return "".join(out)


@lru_cache(maxsize=4096)
def translate(sql: str, has_params: bool = True) -> str:
    """The Postgres spelling of one SQLite statement, or DialectError."""
    masked, held = mask(sql)
    for pattern, why in REFUSED:
        if pattern.search(masked):
            raise DialectError(f"{why}: {sql.strip()[:80]!r}")
    if _OR_IGNORE.search(masked):
        if _ON_CONFLICT.search(masked):
            raise DialectError("INSERT OR IGNORE with its own ON CONFLICT clause is not spelled here")
        masked = _OR_IGNORE.sub("INSERT INTO", masked)
        body = masked.rstrip()
        trailing = masked[len(body):]
        if body.endswith(";"):
            body, trailing = body[:-1], ";" + trailing
        ret = _RETURNING.search(body)
        if ret:
            body = body[:ret.start()] + "ON CONFLICT DO NOTHING " + body[ret.start():]
        else:
            body = body + " ON CONFLICT DO NOTHING"
        masked = body + trailing
    masked = _LOWER.sub(LOWER_FUNCTION + "(", masked)
    masked = _IFNULL.sub("COALESCE(", masked)
    masked = _CAST_INT.sub("AS BIGINT)", masked)
    masked = _CAST_REAL.sub("AS DOUBLE PRECISION)", masked)
    masked = _rewrite_scalar_max_min(masked)
    _check_like(masked)
    masked = _like_without_escape(masked, held)
    _check_sum(masked)
    if has_params:
        # psycopg's placeholder scan is textual and reads string literals
        # too (`'a%b'` counts `%b` as a placeholder; `'gate:%'` is refused:
        # measured), so with parameters EVERY `%` is doubled, the masked
        # literals' included; without parameters psycopg scans nothing and
        # none is.
        masked = masked.replace("%", "%%")
        held = [h.replace("%", "%%") for h in held]
    masked = masked.replace("?", "%s")
    return unmask(masked, held)


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

TYPE_MAP = {"INTEGER": "BIGINT", "REAL": "DOUBLE PRECISION", "TEXT": 'TEXT COLLATE "C"', "BLOB": "BYTEA"}
_CREATE_TABLE = re.compile(r"^\s*CREATE\s+TABLE\s+(IF\s+NOT\s+EXISTS\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)\s*$",
                           re.I | re.S)
_CREATE_INDEX = re.compile(r"^\s*CREATE\s+(UNIQUE\s+)?INDEX\s+(IF\s+NOT\s+EXISTS\s+)?", re.I)
_ALTER_ADD = re.compile(r"^\s*ALTER\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)\s+ADD\s+COLUMN\s+([A-Za-z_][A-Za-z0-9_]*)\s+(.*)$",
                        re.I | re.S)
_REFERENCES = re.compile(r"\bREFERENCES\s+[A-Za-z_][A-Za-z0-9_]*\s*(\([^)]*\))?", re.I)
_TABLE_CONSTRAINT = re.compile(r"^\s*(UNIQUE|PRIMARY\s+KEY|CHECK|FOREIGN\s+KEY|CONSTRAINT)\b", re.I)


def _split_top_level(text: str, sep: str = ",") -> list[str]:
    parts, depth, start = [], 0, 0
    for k, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append(text[start:k])
            start = k + 1
    parts.append(text[start:])
    return [p for p in parts if p.strip()]


def map_column_type(decl: str) -> tuple[str, bool]:
    """`TYPE rest…` with the type mapped, `REFERENCES` dropped and
    `AUTOINCREMENT` lifted out (returned as the second value: the trigger
    owns it)."""
    tokens = decl.strip().split(None, 1)
    typ = tokens[0].upper()
    rest = tokens[1] if len(tokens) > 1 else ""
    if typ not in TYPE_MAP:
        raise DialectError(f"column type {typ!r} is not spelled here")
    autoinc = bool(re.search(r"\bAUTOINCREMENT\b", rest, re.I))
    rest = re.sub(r"\bAUTOINCREMENT\b", "", rest, flags=re.I)
    rest = _REFERENCES.sub("", rest)
    rest = re.sub(r"\s+", " ", rest).strip()
    return (TYPE_MAP[typ] + (" " + rest if rest else "")).strip(), autoinc


def autoinc_trigger_ddl(table: str, column: str) -> str:
    """SQLite's AUTOINCREMENT, as a trigger over a transactional sequence row
    (measured on SQLite: ids 1, 2, 3; a hit consumes a value; a rollback
    restores it; an explicit id moves the sequence; never reused)."""
    fn = f"nexus_autoinc__{table}"
    return f"""
CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger LANGUAGE plpgsql AS $nexus$
DECLARE mx bigint; nxt bigint;
BEGIN
  IF NEW.{column} IS NULL THEN
    SELECT COALESCE(MAX({column}), 0) INTO mx FROM {table};
    INSERT INTO {SEQUENCE_TABLE} (name, seq) VALUES ('{table}', 0) ON CONFLICT (name) DO NOTHING;
    UPDATE {SEQUENCE_TABLE} SET seq = GREATEST(seq, mx) + 1 WHERE name = '{table}' RETURNING seq INTO nxt;
    NEW.{column} := nxt;
  ELSE
    INSERT INTO {SEQUENCE_TABLE} (name, seq) VALUES ('{table}', NEW.{column})
      ON CONFLICT (name) DO UPDATE SET seq = GREATEST({SEQUENCE_TABLE}.seq, EXCLUDED.seq);
  END IF;
  RETURN NEW;
END $nexus$;
CREATE OR REPLACE TRIGGER {fn} BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION {fn}()""".strip()


def rowid_trigger_ddl(table: str) -> str:
    """SQLite's implicit rowid for a table without AUTOINCREMENT: max + 1."""
    fn = f"nexus_rowid__{table}"
    return f"""
CREATE OR REPLACE FUNCTION {fn}() RETURNS trigger LANGUAGE plpgsql AS $nexus$
BEGIN
  IF NEW.rowid IS NULL THEN
    SELECT COALESCE(MAX(rowid), 0) + 1 INTO NEW.rowid FROM {table};
  END IF;
  RETURN NEW;
END $nexus$;
CREATE OR REPLACE TRIGGER {fn} BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION {fn}()""".strip()


#: Run once per schema before any store DDL: the sequence table the id
#: triggers write, the single-writer lock's holder and takeover tables (W3b
#: part 3, D8), and SQLite's ASCII-only LOWER.
SUPPORT_DDL = f"""
CREATE TABLE IF NOT EXISTS {SEQUENCE_TABLE} (name TEXT COLLATE "C" PRIMARY KEY, seq BIGINT NOT NULL);
CREATE TABLE IF NOT EXISTS {LOCK_TABLE} (lock_key TEXT COLLATE "C" PRIMARY KEY, holder TEXT COLLATE "C" NOT NULL);
CREATE TABLE IF NOT EXISTS {LOCK_LOG_TABLE} (at TEXT COLLATE "C" NOT NULL, event TEXT COLLATE "C" NOT NULL);
CREATE OR REPLACE FUNCTION {LOWER_FUNCTION}(t text) RETURNS text LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
  AS $nexus$ SELECT translate(t, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz') $nexus$;
""".strip()


def _translate_create_table(stmt_masked: str, held: list[str]) -> list[str]:
    m = _CREATE_TABLE.match(stmt_masked)
    if not m:
        raise DialectError(f"CREATE TABLE not understood: {unmask(stmt_masked, held)[:80]!r}")
    if_not, table, body = m.group(1), m.group(2), m.group(3)
    cols, constraints, autoinc_col = [], [], None
    for part in _split_top_level(body):
        part = part.strip()
        if _TABLE_CONSTRAINT.match(part):
            if re.match(r"^\s*(FOREIGN\s+KEY|CONSTRAINT)\b", part, re.I):
                continue                                  # not enforced on SQLite (D4)
            constraints.append(re.sub(r"\s+", " ", part))
            continue
        name, _, decl = part.partition(" ")
        mapped, autoinc = map_column_type(decl)
        if autoinc:
            autoinc_col = name
        cols.append(f"{name} {mapped}")
    if table in ROWID_TABLES:
        cols.append("rowid BIGINT")
    ddl = (f"CREATE TABLE {'IF NOT EXISTS ' if if_not else ''}{table} (\n    "
           + ",\n    ".join(cols + constraints) + "\n)")
    out = [unmask(ddl, held)]
    if autoinc_col:
        out.append(autoinc_trigger_ddl(table, autoinc_col))
    if table in ROWID_TABLES:
        out.append(rowid_trigger_ddl(table))
    return out


def translate_ddl(script: str) -> str:
    """The Postgres spelling of a SQLite DDL script (an `executescript`
    body), or DialectError for a statement it does not know."""
    masked, held = mask(script)
    out: list[str] = []
    for stmt in _split_top_level(masked, ";"):
        if not stmt.strip():
            continue
        if _CREATE_TABLE.match(stmt):
            out.extend(_translate_create_table(stmt, held))
        elif _CREATE_INDEX.match(stmt):
            out.append(unmask(re.sub(r"\s+", " ", stmt.strip()), held))
        elif _ALTER_ADD.match(stmt):
            table, col, decl = _ALTER_ADD.match(stmt).groups()
            mapped, _ = map_column_type(decl)
            out.append(f"ALTER TABLE {table} ADD COLUMN {col} {unmask(mapped, held)}")
        else:
            raise DialectError(f"DDL not spelled here: {unmask(stmt, held).strip()[:80]!r}")
    return ";\n".join(s.rstrip().rstrip(";") for s in out) + ";"


__all__ = ["DDL_FIRST_WORDS", "DML_FIRST_WORDS", "DialectError", "LOCK_LOG_TABLE", "LOCK_TABLE", "LOWER_FUNCTION",
           "REFUSED", "ROWID_TABLES", "SEQUENCE_TABLE", "SUPPORT_DDL", "SUPPORT_TABLES", "TYPE_MAP",
           "autoinc_trigger_ddl", "first_keyword", "is_ddl", "is_dml", "map_column_type", "mask",
           "rowid_trigger_ddl", "translate", "translate_ddl", "unmask"]
