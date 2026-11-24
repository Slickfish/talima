# The ids the documents use

The pages explain behaviour by the ids of the work that built it: `W3b`, "law 6", `B-17`, `FZ-2`. This page says
what each kind of id is and where to read about it. None is needed to run NEXUS. Most are defined in the project's
development record (its worklist, item specs, rulings and ledger), which is not published; where a published page
holds the definition, the table names it. File paths in the code's comments and docstrings (`run/specs/W9.md`,
`RUN_WORKLIST.md`, `CLAUDE.md`, `BACKLOG.md`) cite the same record, and so does "BACKLOG" before an id in a page
("BACKLOG W9-B9": that line of the record's ledger, a known gap the page is describing).

| An id like | What it names | Where to read about it |
|---|---|---|
| `W0` to `W15`, `W3b`, `W10b` | An item of the work that built version 1.0: a unit of work with a spec, tests and a close. "W4 part 3" is a part of one | the development record (what each built, with its spec and its build) |
| `run/specs/W4.md`, "D14" in it | An item's spec, written before its code, and a numbered entry in it, whose letter each spec sets: in most, `D` a decision and `C` a claim registered before the tests that measure it (W4's `A1` is an answer from its establish step) | the development record |
| `D1` to `D8` | The run's deliverables (`D7`: these documents for a cold reader) | the development record |
| "law 6", "law 15" | The working rules the work kept, never relaxed (law 6: consent and confirmation never default to true; law 15: an instance's own scripts only read it). There are seventeen | `CONTRIBUTING.md` states the ones a contributor meets first; the numbered list is in the development record, which also holds the five the pages do not state (among them law 3: no belief is deleted and no sealed record rewritten; law 9: every guard has a derivation source and a planted companion) |
| "Law 3", "Law 5" (capital L) | The engine's laws (Law 3: confirm before pulling a model, seeding, deleting or archiving; Law 5: a context of at most 8,192 tokens). In these pages a capital letter with an Arabic number means these eight, and the small letter a working rule | `docs/CONTRACTS.md`, "The laws" |
| "Law VII", "Law XII"; a law's number in a code comment | The code's comments were written over a longer time than the pages and do not keep the pages' rule: a capital letter there can be a working rule ("Law 15", "Law 16"). Roman numerals are mostly the eleven laws of the architecture specification (Law I: edges are potentials, not facts; Law VI: state is immutable; Law IX: the scheduler routes capabilities, not models) and Law XII of its later delta (work is attributed to units, never sequenced); but "Law V" in the code is the engine's Law 5, the context ceiling, and not the specification's fifth law. "Law 8" beside a loss that is declared is the engine's Law 8 as the development record glosses it: "never silent truncate". Read a comment's law by the sentence it stands in, not by its number | the development record; the architecture specification and its delta are not published |
| "Amendment 7" | A change to how the work is run (Amendment 7: no workflow runs on a push) | the development record; what Amendment 7 changed is in `CONTRIBUTING.md`, "CI" |
| "Ruling 6" | A decision taken during the work and recorded for review | the development record |
| `W6-B3`, `B-17`, `B-49`, `OQ-4` | A finding or a decision in the ledger: `W6-B3` was found during W6; a bare `B-` id is older than the run | the development record's ledger |
| `FZ-2`, `AS-1`, `AT`, `AW`, `AX.1`, `CP`, `C-38`, `MCP-2`, `LS` | A packet of the history before the run: a named change, with its reasons and its tests | `docs/CONTRACTS.md`, "The record's contracts", for the record's (AR, AS-1, and the items W5 to W10b); the rest in the development record |
| `I-4`, `I-6` | An invariant of the system (I-4: citation confers durability; I-6: declared loss applies to the system's own telemetry, so a state is declared, never left silent) | the development record |
| "Phase 4", "v3.1 Phase 3" | A phase of the original build (Phase 1 to 10d), or of the v3.1 delta specification (Phase 1 to 4) | `docs/ARCHITECTURE.md` §10; the development record |
| "tier 1" (an item's) | An item's weight, which sets its time box (tier 1 four sessions of work, tier 2 two, tier 3 one); a tier 1 item touches what the record seals or what the model is sent (as W3b's spec says of itself) | the development record |
| tier 0 to 4 (a claim's) | A claim's prominence tier in the store, not an item's | `docs/CONTRACTS.md`, Law 4 |
