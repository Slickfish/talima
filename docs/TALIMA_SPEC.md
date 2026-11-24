# Talima v1: the record and verification specification

Talima is an open standard for records of what an AI system was given and what it answered, sealed so that a
stranger can verify them offline. This document specifies version 1: the record, its canonical encoding, its
hashes, the seal, the chain of blocks, the Merkle proofs, the keys and signatures, the documents a holder passes
on (a credential, a receipt, a chain), and how each is verified. It is written to be implemented without reading
any implementation. NEXUS is the reference producer and `talima-verify` the reference verifier; `docs/TALIMA.md`
is the short introduction.

## 1. Roles and conformance

A **producer** writes records, seals them into blocks, signs the blocks and issues documents. A **holder** keeps a
document and chooses what it discloses. A **verifier** checks a document with nothing but the document and the
producer's published identity.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be read as in RFC 2119 and RFC 8174, when in
capitals. In this document:

- Every MUST is a rule for what a producer emits. It carries an identifier (`M-CAN-1`, ...) and can be checked
  from documents alone. Section 13 names, for each, the failure a verifier reports when it is broken.
- A requirement that no document can show (that a salt is random, that a key is kept private) is a SHOULD, and the
  text says why it cannot be more.

**A producer conforms** when every document it emits satisfies every rule of this document. The test the project
runs is that `talima-verify` accepts them: every conforming document passes it. It is a test and not the whole
definition, because the reference verifier also accepts two older forms that no conforming producer writes
(Appendix A), and no verifier sees what no document shows (the SHOULDs). **A verifier conforms** when it gives
the verdict stated for every test vector of section 14 and reports the failure section 13 names for each broken
rule; whether it accepts Appendix A's older forms is its own choice.

## 2. Notation

A *hash* is SHA-256 (FIPS 180-4). Written as text, a hash is 64 lower-case hexadecimal characters; every byte string
this document writes as text is lower-case hexadecimal, an anchor's `token` excepted (base64, section 12). `a || b` is concatenation of bytes. `H(x)` is the SHA-256
of the bytes `x`. `canonical(v)` is section 3's encoding of a JSON value. A *number* is a JSON number; an *integer*
is a number written with no fraction and no exponent. JSON is RFC 8259.

## 3. The canonical encoding

Everything that is hashed is first encoded one way. `canonical(v)` is the JSON text of `v`, as ASCII bytes, under
these rules.

- **M-CAN-1.** The members of an object MUST be written in ascending order of their names, compared by Unicode
  code point.
- **M-CAN-2.** No white space MUST appear outside a string: members are separated by `,` and a name from its value
  by `:`, with nothing else between tokens.
- **M-CAN-3.** A string MUST be written in ASCII: the characters U+0020 to U+007E as themselves, except `"` as
  `\"` and `\` as `\\`; U+0008, U+0009, U+000A, U+000C and U+000D as `\b`, `\t`, `\n`, `\f` and `\r`; every other
  character as `\u` and four lower-case hexadecimal digits, a character above U+FFFF as its UTF-16 surrogate pair
  written that way. `/` is not escaped.
- **M-CAN-4.** A number MUST be written in one of two forms, and the two are different values. An integer is
  written in decimal with no leading zero and a `-` when negative. Any other number is an IEEE 754 binary64 value,
  written with the fewest significant digits that read back as that value and, where several strings of that
  length do, the one nearest the value (of two equally near, the one whose last digit is even), which is what
  Python's `repr` and ECMAScript's number-to-string write: with a decimal point and at least one
  digit after it (`1.0`, `0.0001`, `1700000000.0001`) when its decimal exponent (the power of ten of its first
  significant digit) is at least -4 and less than 16; otherwise as one digit, an optional point and more digits,
  `e`, a sign and at least two exponent digits (`1e+16`, `1e-05`, `1.5e+300`). Zero is `0.0`, and negative zero
  `-0.0`.
- **M-CAN-5.** A value that JSON cannot carry (a NaN, an infinity) MUST NOT appear anywhere in a document.
- **M-CAN-6.** An object MUST NOT give one name twice.
- **M-CAN-7.** A string MUST NOT hold an unpaired surrogate: half of a UTF-16 pair is not a character.
- **M-CAN-8.** A byte string written as text (a hash, a salt, a key, a signature, a leaf, a path element) MUST be
  lower-case hexadecimal.

`true`, `false` and `null` are written so, and an array keeps its order.

An integer SHOULD stay within 2^53 - 1 either side of zero: many readers hold every number as binary64 and lose
a larger integer silently, which no document can show. No field version 1 defines needs one.

A document itself (the file a holder passes on) need not be canonical: it is read as JSON, and each part that a
hash covers is re-encoded by the verifier. So a number in a document has to be the number that was hashed: a
producer that hashes `1.0` and writes `1` in the document has changed the value, and the hash no longer holds.

A document's file is UTF-8 (a byte-order mark is passed over; the reference verifier also reads UTF-16 with its
mark). M-CAN-5 to M-CAN-7 are held as the file is read, whatever member breaks them, hashed or not. M-CAN-8 is
held wherever a verifier reads a byte string of the document's own structure. It decodes a commitment, a salt, a
leaf, a path element, a signature, the key it checks a signature against and, under the older layout 1, a
block's sealed list; the rest it compares with a value it computed, which is lower-case (section 13 names what
each is reported as). A hash inside a record's or an action's fields is the producer's content, committed like
any other text: only a record's core hash fields are held to their form (M-REC-2). A number written with a fraction or an exponent is read as the binary64 value nearest it, and one
that no binary64 value is near (`1e400`) is refused like an infinity. A member of a document's own structure that
this document types as an integer (a version, an index, a size, a period's block) is an integer in the file:
`1.0` and `true` are not. A record's fields are not that structure; their names and types are the producer's
(section 4), and a record may call a field `index`. Nor is a member this document does not name: a document MAY
hold others, a verifier takes them as they are, and what one kind of document types another may name as it likes
(a credential may hold a member called `blocks`).

## 4. The record

A record is one JSON object, its *body*. The **record hash** is `H(canonical(body))`.

- **M-REC-1.** The body MUST hold the eight core fields below.
- **M-REC-2.** Each core field MUST have the type the table gives.
- **M-REC-3.** The record hash MUST be `H(canonical(body))`, over the whole body and nothing else.
- **M-REC-4.** A field whose value is its default MUST be absent from the body: `scope` when it would be
  `default`, `record_scope` when it would be `full_turn`, `learning_mode` when it would be `live`,
  `identity_mechanism` when it would be `asserted`; an empty string or `null` there counts as the default. So a
  record that states none of them has one body and one hash.

| Core field | Type | What it is |
|---|---|---|
| `lineage_id` | string | The record's identifier, unique to the producer |
| `ts` | number | When the record was written, in seconds since 1970-01-01T00:00:00Z |
| `query_hash` | hash | The hash of the question's text (UTF-8) |
| `injected` | array | What the system put before the model for this answer; the members are the producer's |
| `contradictions` | array | What the system found in conflict while answering; the members are the producer's |
| `model_id` | string | The model that answered, as the producer knows it |
| `prompt_prefix_hash` | hash | The hash of the fixed part of the prompt (UTF-8) |
| `response_hash` | hash | The hash of the answer as it was received |

A body MAY hold further fields. Version 1 defines these; each is present only when it has something to say:

| Field | Type | Present when |
|---|---|---|
| `scope` | string | The caller named a scope other than `default` |
| `request_hash` | hash | The whole request sent to the model was hashed |
| `work_unit_id`, `accountable_identity` | string | The caller attributed the record to a unit of work, or to a person (asserted, not authenticated, unless `identity_mechanism` says otherwise) |
| `boundary_asserted`, `boundary_asserted_by` | string | The caller asserted a decision boundary |
| `learning_mode` | string | Learning was not live when the record was written (`frozen`) |
| `answer_hash`, `reasoning_hash` | hash | The answer and the model's reasoning were separated and hashed apart |
| `record_scope` | string | The record is not a full turn: `mcp_block` or `intercepted` (below) |
| `inference_seed` | integer | A seed was sent to the model |
| `model_digest`, `inference_config_hash`, `environment_hash` | string | The model's build, its settings and the environment were captured |
| `capture_notes` | string | Notes on what was captured, as canonical JSON text: why a captured field is absent and, for an intercepted exchange, the tool uses its response asked for |
| `identity_mechanism` | string | `accountable_identity` was established by other than assertion (`proxy`) |
| `envelope_version`, `forecast` | any | The governance policy in force, and a forecast made before the answer |

A verifier accepts a field it does not know: it is committed like any other. It holds the core fields' types
(M-REC-2); the type of any other field is the producer's to keep.

**The record's scope** says who observed what. Absent (`full_turn`): the producer ran the model, and every field
is its own observation. `mcp_block`: the turn was split at the model call; the producer supplied the context and
recorded the answer a client sent, so `model_id` is `client-reported:<name>` and `response_hash` is the client's
claim. `intercepted`: the producer relayed one exchange between a harness and a model provider; `request_hash` is
over the request's bytes as the harness sent them, `response_hash` over the response's bytes as the provider
returned them, and `model_id` is `upstream-reported:<name>`. These forms of `model_id` tell a reader who made
the claim; a verifier does not hold them.

## 5. Commitments

A record is sealed together with one salted commitment per field of its body, so that a holder can later disclose
some fields and leave the rest as commitments that say nothing.

- **M-COM-1.** The commitment of a field MUST be `H(salt || canonical(value))`.
- **M-COM-2.** A salt MUST be exactly 32 bytes. There is no separator between the salt and the value, so a salt of
  another length moves bytes between them.
- **M-COM-3.** A record MUST have one commitment for each field of its body, under the field's name: a field
  with no commitment cannot be disclosed.
- **M-COM-4.** The **commitment root** MUST be the Merkle root (section 6) of the leaves
  `name (UTF-8) || 0x00 || commitment`, one per field, in ascending order of name, the names compared by Unicode
  code point as M-CAN-1 compares an object's.

A salt SHOULD be 32 bytes from a cryptographic random source, fresh for every field of every record. No document
can show how a salt was made; a predictable salt lets a short value be recovered from its commitment by trial.

## 6. Merkle trees

Trees are those of RFC 6962, section 2.1.

- **M-MRK-1.** The root of a list of leaves MUST be: for no leaves, `H("")`; for one leaf `d`, `H(0x00 || d)`; for
  `n > 1` leaves, `H(0x01 || root(first k) || root(the rest))`, with `k` the largest power of two smaller than `n`.
- **M-MRK-2.** An inclusion path MUST be the audit path of RFC 6962, section 2.1.1, for the leaf's index in a tree
  of the stated size, listed from the leaf's sibling up to the root's child. A verifier checks it as RFC 9162,
  section 2.1.3.2, describes.

## 7. Blocks and the chain

Records are sealed in **blocks**. A block has a *header*, which is what its hash covers:

| Header member | Type | What it is |
|---|---|---|
| `layout` | integer | `2` |
| `index` | integer | The block's position in the chain, from 0 |
| `timestamp` | number | When the block was appended, in seconds since the epoch; the producer's own word |
| `fingerprint_hash` | hash | `H(canonical(fingerprint))`, the fingerprint being an object the producer keeps beside the block (the leaf lists it sealed, a key it announced, anything else it binds) |
| `roots` | object | For each seal the block makes, by name: `{"root": hash, "size": integer}` |
| `snapshot_path`, `snapshot_hash` | string, hash | A file the producer snapshots at this block and its hash; with no file, `""` and `H("")` (the reference producer, when a file it names is missing, gives the hash of the path's text) |
| `previous_hash` | hash | The hash of the block before; for block 0, 64 zeros |
| `snapshot_missing` | `true` | Present when no snapshot file existed, and never `false`; a producer that snapshots no file MAY leave it out |
| `kind` | string | Present only when the block is not a snapshot block, and then neither empty nor `snapshot` (`governance`: a block that seals and snapshots nothing) |

A *snapshot block* is one the producer appends with a snapshot of its own state. It is the default kind, said by
absence. An `index` is below 2^64: section 8's statement writes it in 8 bytes. A header MAY hold members this
table does not name; they are hashed with the rest (M-BLK-1), and a verifier takes them as they are.

- **M-BLK-1.** The **block hash** MUST be `H(canonical(header))`.
- **M-BLK-2.** A header MUST hold the first eight members of the table (through `previous_hash`), each of the
  type given, and a root's `size` MUST be a non-negative integer.
- **M-BLK-3.** `snapshot_missing` MUST be absent unless it is `true`, and `kind` MUST be absent when the block is a
  snapshot block. A header says a default by saying nothing.
- **M-BLK-4.** A root named `lineage` MUST be the Merkle root of one 64-byte leaf per record sealed:
  `record hash || commitment root`.
- **M-BLK-5.** A root's `size` MUST be the number of leaves the root is over.
- **M-CHN-1.** The blocks of a chain MUST have the indices 0, 1, 2, ... in order, with none missing.
- **M-CHN-2.** A block's `previous_hash` MUST be the block hash of the block before it.
- **M-CHN-3.** Block 0's `previous_hash` MUST be 64 zeros, and no other block's is.

Version 1 names five roots. `lineage`: the records (M-BLK-4). `commit`: held commitments, each leaf one 32-byte
commitment (section 10). `action`: governed actions, each leaf one 32-byte action record hash (section 10).
`governance` and `binding` are the reference producer's own: its policy changes; and the records it sealed before
they had a commitment root, each bound to one later by a `binding` leaf of 64 bytes, made as a `lineage` leaf is
(Appendix A). No rule of this document governs what a `governance` leaf holds. Where a chain document lists a
root's leaves (section 11), a `lineage`, `binding`, `commit` or `action` leaf is held to its length
(`leaf_malformed`); a `governance` leaf, and a leaf of a root this section does not name, is of any length.

The reference verifier accepts one older form there and says so in a note (`bare_hash_leaves`): a `lineage` leaf
of 32 bytes, the record hash alone (Appendix A). That is its own choice and no part of conformance: a conforming
producer writes no such leaf (M-BLK-4), so no conforming document's verdict turns on it, and another verifier may
refuse one.

A block's time is the producer's word. Section 12 says how a third party dates a block.

## 8. Keys, the identity document and signatures

A producer signs each block with an Ed25519 key (RFC 8032).

- **M-KEY-1.** A key's `key_id` MUST be `H(public key)`, the public key being its 32 bytes.
- **M-SIG-1.** A block's signature MUST be the Ed25519 signature, by the key named, of the 55-byte statement
  `"NEXUS-BLOCK-V1" || 0x00 || index (8 bytes, big-endian) || block hash (32 bytes)`.
- **M-SIG-2.** A document MUST carry a signature for every block it rests on.
- **M-SIG-3.** A signature MUST name its block by both `block_index` and `block_hash`.
- **M-KEY-2.** A key MUST sign only blocks inside its period: from the block that announced it (`announced_in`)
  up to, and not including, `until_block`.

The **identity document** is what a producer publishes so that a verifier can pin its keys:

```
{"algorithm": "ed25519", "public_key": <64 hex>, "key_id": <hash>, "created_at": <number>,
 "keys": [{"key_id": <hash>, "public_key": <64 hex>, "algorithm": "ed25519",
           "reason": "first" | "rotation", "previous_key_id": <hash> | null,
           "announced_in": <integer> | null, "until_block": <integer> | null,
           "active_from": <number> | null, "active_to": <number> | null}, ...]}
```

The top-level `public_key` and `key_id` are the current key's, repeated for a reader, and `created_at` is when
that key was made; a verifier reads `keys` alone. A document and a key entry MAY hold other members
(the reference producer adds `signing`); `algorithm` is `ed25519` in version 1, and a verifier does not read it.

`keys` lists every key the producer has used, oldest first. A key's period is counted in blocks, because a block's
index only grows while a clock can be set back. The current key's period is open (`until_block: null`). A key that
no block has announced yet has `announced_in: null`, `"pending": true` and `from_block`, the index of the next
block, where its period opens. A block *announces* a key by naming it in its fingerprint as the producer's key
from that block on; a verifier does not read fingerprints, it reads the period the identity document states. A
key that states no period at all, neither `announced_in` nor `from_block` nor `until_block` (one public key
pinned by hand), is taken as active at every block. One that states an end and no start has no period a `seal`
signature can be held to: nothing says where it opened, and a verifier refuses rather than guess
(`signature_outside_key_period`). `active_from` and `active_to` are the times of the period's two ends, for a reader.

A signature in a document is `{"block_index", "block_hash", "key_id", "value", "signed"}`: `value` is the
signature in hexadecimal, and `signed` is `seal` when it was made as the block was appended, or `issue` when it
was made as the document was issued, for a block appended unsigned; any other value, or none, is read as `seal`.
A verifier holds a `seal` signature to the whole period (M-KEY-2) and an `issue` signature to the period's end
only. The label is the document's own word, so a signature presented as `issue` is held to less: what it shows
is that the key signed the block before its period closed, not that the block was appended inside it. The
reference producer writes `issue` for any block it appended unsigned: before it had a key, or while its key
could not be read (Appendix A).

A private key SHOULD never leave the producer, and a verifier SHOULD obtain the identity document from a source
it trusts rather than from the document it is checking. Neither can be shown by a document: a document checked
against the keys it lists itself proves nothing about who sealed it, and a verifier says so (`key_unpinned`).

## 9. Proofs and credentials

A **proof** places one record in one block:

```
{"proof_version": 1, "state": "sealed", "seal": "lineage",
 "record_hash": <hash>, "commitment_root": <hash>,
 "leaf": <128 hex: record hash || commitment root>, "leaf_index": <integer>, "tree_size": <integer>,
 "path": [<hash>, ...],
 "block": {"layout": 2, "index": <integer>, "block_hash": <hash>, "header": {...}}}
```

A proof MAY also name its record, in a member `lineage_id` (M-DOC-6). `proof_version` is `1` and `seal` is
`lineage` in version 1; they are for a reader, and a verifier holds neither.

`block` is a **block reference**: the block's layout, its index, its hash and its header. `layout` is 2, and a
block labelled otherwise is `block_malformed`. (Under a proof the reference verifier also reads the older layout
1, whose reference carries the block's `body` in place of a header: Appendix A.) A receipt (section 10) and a
chain document (section 11) hold the same reference, and rest on layout-2 blocks only.

A **credential** is a proof with the record's commitments, the fields its holder discloses, and what a verifier
needs to check the block's signature:

```
{"credential": "nexus.credential", "version": 1,
 "commitment_root": <hash>, "commitments": {<field>: <hash>, ...},
 "disclosed": {<field>: {"value": <the field's value>, "salt": <64 hex>}, ...},
 "proof": {...}, "signatures": [...], "keys": [...],
 "scope": {...}, "decision_fields": [<field>, ...], "anchors": [...]}
```

- **M-DOC-1.** A credential MUST be named `"credential": "nexus.credential"` with `"version": 1`.
- **M-DOC-2.** A proof's `state` MUST be `sealed`: a record that no block holds has no proof.
- **M-DOC-3.** The proof's `leaf` MUST be the record hash followed by the commitment root, and the credential's
  `commitment_root` MUST be the one in that leaf.
- **M-DOC-4.** The value and salt of each field in `disclosed` MUST open that field's commitment (M-COM-1).
- **M-DOC-5.** Every field named in `decision_fields` MUST have a commitment.
- **M-DOC-6.** A proof MUST NOT carry `lineage_id` unless its credential discloses that field, with that value.
  A producer SHOULD carry it then; a verifier does not require it.

`commitments` holds every field of the body, disclosed or not, so the names of a record's fields are always
visible and their values only when disclosed. `scope` is the producer's statement about the record for a reader
(the reference producer writes the record's scope and a note); it is under no hash and no signature, and a
verifier does not hold it to the record: what a record says of its own scope is its committed field
`record_scope`. `keys` is the producer's key list; a verifier pins the keys from the identity document, not from
here. `decision_fields` names the committed fields that state the policy in force and its forecast. `disclosed`,
`keys`, `scope`, `decision_fields` and `anchors` MAY be absent.

When a credential discloses **every** field, the verifier rebuilds the body from the disclosures and holds its
hash to M-REC-3. With fewer, the record hash is opaque to it, and that is by design: the body holds a random
identifier, so no guess at an undisclosed field can be tested against the hash. The other rules of section 4 it
holds as far as a credential shows the record: the core fields' names always (M-REC-1), and a field's type and
its default whenever that field is disclosed (M-REC-2, M-REC-4). A `lineage_id` SHOULD carry at
least 128 random bits for that reason; a document cannot show that it does.

## 10. Receipts

A **commitment receipt** is for content committed now and revealed later. The holder keeps the content and its
salt; the receipt carries neither.

```
{"receipt": "nexus.commitment", "version": 1, "commitment_id": <string>, "commitment": <hash>,
 "held_at": <number>, "leaf_index": <integer>, "tree_size": <integer>, "path": [<hash>, ...],
 "block": {"layout": 2, "index": <integer>, "block_hash": <hash>, "header": {...}},
 "signatures": [...], "keys": [...], "anchors": [...]}
```

- **M-RCP-1.** A commitment receipt MUST be named `"receipt": "nexus.commitment"` with `"version": 1`.
- **M-RCP-2.** The commitment MUST be `H(salt || canonical(content))`, the content being a JSON string (the text
  exactly as the holder kept it, a final line end included) and the salt 32 bytes.
- **M-RCP-3.** The commitment MUST be a leaf of the block's `commit` root, at `leaf_index` in a tree of
  `tree_size`, which MUST be the size the header seals.

`commitment_id` is the producer's name for the commitment, and `held_at` the time the producer took it in, for
a reader; neither is under the proof, and a verifier reports the block's own time.

An **action receipt** is for a request the producer sent on a person's behalf, or was told about, or saw pass.

```
{"receipt": "nexus.action", "version": 1, "action_id": <string>, "fields": {...}, "record_hash": <hash>,
 "leaf_index": <integer>, "tree_size": <integer>, "path": [<hash>, ...],
 "block": {"layout": 2, "index": <integer>, "block_hash": <hash>, "header": {...}},
 "signatures": [...], "keys": [...], "anchors": [...],
 "disclosed": {"accountable_identity": {"value": <string> | null, "salt": <64 hex>}}}
```

- **M-ACT-1.** An action receipt MUST be named `"receipt": "nexus.action"` with `"version": 1`.
- **M-ACT-2.** `fields` MUST hold exactly these eighteen members: `action_id`, `kind`, `evidence_class`, `method`,
  `target`, `request_hash`, `credentials_json`, `lineage_id`, `trigger_route`, `identity_commitment`,
  `identity_mechanism`, `signature_key_id`, `signature`, `requested_at`, `state`, `status`, `response_hash`,
  `completed_at`; and the receipt's own `action_id` MUST be the one among them.
- **M-ACT-3.** The action's `record_hash` MUST be `H(canonical(fields))`.
- **M-ACT-4.** The record hash MUST be a leaf of the block's `action` root, at `leaf_index` in a tree of
  `tree_size`, which MUST be the size the header seals.
- **M-ACT-5.** When the receipt discloses the accountable identity, its value and salt MUST open
  `identity_commitment` (M-COM-1, M-COM-2).

| Field | Type | What it is |
|---|---|---|
| `action_id` | string | The producer's identifier of the action |
| `kind` | string | What was done (`web_search`, `model_exchange`, `tool_use`, or a client's own word) |
| `evidence_class`, `state` | string | Below |
| `method`, `target` | string | How and where: an HTTP method and a URL without its query, or the producer's own names |
| `request_hash` | hash | The hash of what was sent, or asked |
| `credentials_json` | string | Below |
| `lineage_id`, `trigger_route` | string or null | The record the action was taken for, and what set it off |
| `identity_commitment` | hash | The salted commitment of the accountable identity (of `null`, when no one is named) |
| `identity_mechanism` | string or null | How the identity was established |
| `signature_key_id`, `signature` | string or null | The RFC 9421 signature the producer put on the request, when it signs its requests |
| `requested_at` | number | When the request was recorded, before it left |
| `status` | string or null | The outcome in the producer's words (an HTTP status as text; `reported`) |
| `response_hash` | hash or null | The hash of what came back |
| `completed_at` | number or null | When the outcome was written |

A verifier holds the set of names (M-ACT-2) and the hash over them (M-ACT-3); the types are the producer's to
keep.

`disclosed` is optional: a receipt without it still proves the sealed action and names no one. The reference
producer adds a `per_person` member beside it, a declaration for a reader that the receipt names a person.

`evidence_class` says what the producer saw: `runtime`, it sent the request itself; `client_reported`, a client
told it; `intercepted`, it saw a model ask for a tool and a harness report a result, and never saw the tool run.
`state` is `requested`, `completed`, `failed` or `incomplete`. `credentials_json` is the canonical text of a list
of `{"header", "fingerprint"}`: a credential is recorded by its header's name and the first 16 hexadecimal
characters of the hash of its value, never by its value. The accountable identity is in the fields as a salted
commitment, so an action can be proved to a stranger without naming the person.

## 11. The chain document

A chain document carries a producer's blocks, so that the links between them can be checked.

```
{"chain": "nexus.chain", "version": 1,
 "blocks": [{"layout": 2, "index": <integer>, "block_hash": <hash>, "header": {...},
             "leaves": {<root name>: [<hex>, ...], ...}}, ...],
 "signatures": [...], "keys": [...], "anchors": [...]}
```

- **M-CHD-1.** A chain document MUST be named `"chain": "nexus.chain"` with `"version": 1`.
- **M-CHD-2.** Where a block lists the leaves of a root, they MUST be all of that root's leaves in order: the
  header's `size` is their number (M-BLK-5) and its `root` their Merkle root (M-MRK-1).

`blocks` holds the chain's blocks in order, so every rule of section 7 applies to them as they stand: each block's
hash and header, the indices in order with none missing (M-CHN-1), each link (M-CHN-2) and block 0's previous
hash, which no later block may claim (M-CHN-3). Those three are rules of a chain, and a verifier holds them where
a chain is shown: here. A credential or a receipt shows one block, and what its header says of the block before
it is not checked there. A chain document holds layout-2 blocks only.

`leaves` is optional, root by root: a holder may list the leaves of some roots and not of others, and a root whose
leaves are not listed is taken as sealed. `keys` may be empty: a verifier pins the keys from the identity
document. A document MAY begin after block 0; nothing before its first block is checked then, and the verifier
says so.

## 12. Anchors

A block's `timestamp` is the producer's own word. An **anchor** is a third party's: an RFC 3161 time-stamp over the
block's hash.

```
{"block_index": <integer>, "method": "rfc3161", "authority": <string>, "token": <base64>, "anchored_at": <number>}
```

- **M-ANC-1.** An anchor a document carries MUST hold, as `token`, the base64 of a DER RFC 3161 `TimeStampResp`
  that was granted, whose message imprint is SHA-256 and equals the hash of the block the anchor names, and that
  block MUST be one the document rests on.

A verifier that is given the authority's certificate also checks the time-stamp's signature. Anchors are optional:
a document with none is dated by its producer alone.

## 13. Verification

A verifier is given a document and, to pin the producer's keys, the identity document (or one public key). It
reports **verified** only when every check below holds, and otherwise names each failure.

**A credential.** (1) Its name and version. (2) Every disclosure opens its commitment under a 32-byte salt, and no
commitment is the unsalted hash of its value. (3) The commitments make the commitment root. (4) The proof: its
state; its leaf; the header's shape; the header hashes to the block's hash and states the block's index; the
tree's size is the size the header seals; the path leads from the leaf to the `lineage` root. (5) The root in the
sealed leaf is the credential's. (6) The decision fields are committed. (7) The core fields are committed, each
disclosed core field has its type, and no disclosed field states a default. (8) When every field is disclosed, the
body they make hashes to the record hash. (9) Every block the proof rests on carries a signature that names it by
index and hash, by a key on the pinned list whose id is the hash of its public key, over the statement, inside the
key's period. (10) Each anchor it carries dates the block it names.

**A receipt.** Its name and version; the commitment is opened by the content and salt the holder supplies (a
commitment receipt), or the fields hash to the record hash and a disclosed identity opens its commitment (an action
receipt); then the header, the size, the path to the `commit` or `action` root, the signatures and the anchors, as
for a credential.

**A chain.** Its name and version; each block's header, hash and index; the indices in order; each link, and
block 0's; each listed root; a signature for every block, as for a credential; the anchors.

| Rule | What a producer broke | The failure reported |
|---|---|---|
| M-CAN-1 | object members not in order | `block_hash_mismatch` |
| M-CAN-2 | white space in what was hashed | `block_hash_mismatch` |
| M-CAN-3 | a string not escaped to ASCII | `disclosure_mismatch` |
| M-CAN-4 | a number in another form | `block_hash_mismatch` |
| M-CAN-5 | a NaN or an infinity | `malformed` |
| M-CAN-6 | a name given twice in one object | `malformed` |
| M-CAN-7 | an unpaired surrogate in a string | `malformed` |
| M-CAN-8 | a byte string not in lower-case hexadecimal | `malformed` |
| M-REC-1 | a core field missing | `record_malformed` |
| M-REC-2 | a core field of another type | `record_malformed` |
| M-REC-3 | a record hash that is not the body's | `record_mismatch` |
| M-REC-4 | a default stated | `record_malformed` |
| M-COM-1 | another commitment formula | `disclosure_mismatch` |
| M-COM-2 | a salt that is not 32 bytes | `unsalted_hash` |
| M-COM-3 | a disclosed field with no commitment | `disclosure_mismatch` |
| M-COM-4 | commitment leaves in another order or form | `commitments_mismatch` |
| M-MRK-1 | another tree hash | `commitments_mismatch` |
| M-MRK-2 | a path in another order | `root_mismatch` |
| M-BLK-1 | a block hash that is not the header's | `block_hash_mismatch` |
| M-BLK-2 | a header member missing or of another type | `block_malformed` |
| M-BLK-3 | a default stated in a header | `block_malformed` |
| M-BLK-4 | a lineage leaf that is not the record's | `leaf_mismatch` |
| M-BLK-5 | a size that is not the tree's | `size_mismatch` |
| M-CHN-1 | an index missing or out of order | `index_gap` |
| M-CHN-2 | a link that is not the block before's hash | `chain_link_broken` |
| M-CHN-3 | block 0 with another previous hash | `chain_link_broken` |
| M-KEY-1 | a key id that is not the key's hash | `key_id_mismatch` |
| M-KEY-2 | a block signed outside the key's period | `signature_outside_key_period` |
| M-SIG-1 | another statement signed | `signature_invalid` |
| M-SIG-2 | a block with no signature | `unsigned` |
| M-SIG-3 | a signature that names another block | `signature_invalid` |
| M-DOC-1 | another name or version | `malformed` |
| M-DOC-2 | a proof of an unsealed record | `malformed` |
| M-DOC-3 | a root the seal does not hold | `root_not_sealed` |
| M-DOC-4 | a disclosure that does not open | `disclosure_mismatch` |
| M-DOC-5 | a decision field not committed | `malformed` |
| M-DOC-6 | a proof that names a record its credential does not disclose | `lineage_id_mismatch` |
| M-RCP-1 | another name or version | `malformed` |
| M-RCP-2 | a commitment the content does not open | `reveal_mismatch` |
| M-RCP-3 | a commitment not in the `commit` root | `root_mismatch` |
| M-ACT-1 | another name or version | `malformed` |
| M-ACT-2 | other fields than the eighteen | `malformed` |
| M-ACT-3 | a record hash that is not the fields' | `action_mismatch` |
| M-ACT-4 | an action not in the `action` root | `root_mismatch` |
| M-ACT-5 | an identity that does not open | `disclosure_mismatch` |
| M-CHD-1 | another name or version | `malformed` |
| M-CHD-2 | listed leaves that do not make their root | `roots_mismatch` |
| M-ANC-1 | an anchor that does not date its block | `anchor_invalid` |

M-CAN-1 to M-CAN-4 are broken wherever something was hashed. The table names the failure for the place the
project's tests break each (a block's header; a disclosed string); the same breakage inside a commitment, a
record or an action is reported under that hash's own name. M-CAN-5 to M-CAN-7 are held as the document is read,
before any hash: whatever the member, the document is `malformed`. M-CAN-8 is held where a byte string is read,
and reported by the check that met it. Where the verifier decodes the string (section 3 lists those) the document
is `malformed`. Where it compares the string with a value it computed, the comparison fails under its own name:
`record_malformed` (a record's core hash field), `block_malformed` (a hash or a root inside a header),
`block_hash_mismatch` (a block's hash), `leaf_mismatch` (a proof's record hash or commitment root; a binding's own
are `binding_block_hash_mismatch` and `binding_leaf_mismatch`), `commitments_mismatch` (a credential's commitment root), `signature_invalid` (a
signature's block hash or key id), `key_id_mismatch` (a key id in upper case in the key list and the signature
alike), `action_mismatch` (an action's record hash) and `disclosure_mismatch` (an action's identity commitment).
Other failures may stand beside these: what follows from the first one is reported too.

A rule has more clauses than its row names, and a clause may be reported under another name: a path that does
not fit its tree is named by the path check itself (`index_out_of_range`, `path_too_long`, `path_too_short`)
rather than `root_mismatch`; a tree's size that is not the header's is `size_mismatch`; a listed leaf of the
wrong length is `leaf_malformed`; a member typed as an integer that is not one is `malformed`, or
`block_malformed` in a header; a block labelled a layout its document does not rest on (section 9), and an empty
tree whose root is not the empty root, are `block_malformed`. `key_unpinned` is the verifier's own: it was given no
identity.

A file that cannot be read as a document at all (not JSON, or broken by M-CAN-5 to M-CAN-7) is `malformed`, and
so is an identity that cannot be read or whose `keys` is not a list. The reference verifier then ends with status
2 and says why on its error stream; a document that was read and refused ends with 1. Asked for JSON, it prints
its verdict in both cases. A file that is not there, or an argument that is not one (a key that is not 64
hexadecimal characters; with `--reveal`, a salt that is not whole bytes of hexadecimal), is no verdict: status 2
and a message. What a person
types after `--key` or `--salt` is not a document: its case and its spaces are passed over.

## 14. Conformance and test vectors

`conformance/minimal_producer.py` is a second producer, written from this document alone: one file, the Python
standard library only, importing nothing of NEXUS. It makes a key, three records, three linked blocks, an identity
document, a credential with one field disclosed, a credential with every field disclosed, a commitment receipt
with its content and salt, an action receipt and a chain document, and `talima-verify` accepts each. The project's
tests break each rule of section 13 in it, one at a time, and require the failure the table names.

The vectors of version 1 are files of `tests/fixtures/tb_golden/`, made by the reference producer and committed
before any of this document's rules were named, and a chain document made since from the same store. A conforming
verifier gives each the verdict shown:

| File | SHA-256 | Given to the verifier as | Verdict |
|---|---|---|---|
| `credential.json` | `24523b429b328e11f5a098eb5175f369e840f870edf53a95bac794e3325d6dc5` | `credential.json --identity identity.json` | verified |
| `credential_disclosed.json` | `ff3028231abafe204b057643aed9a7339dfdde8660b2da1bdb120742881e559f` | `credential_disclosed.json --identity identity.json` | verified |
| `credential_tombstoned.json` | `50e7a8af519f34131301166d3b99ae412f96615cef287fcae2924d4d310d0212` | `credential_tombstoned.json --identity identity.json` | verified |
| `commitment_receipt.json` | `2cbc6611da53459a80ebe68c79e367d0066d4644fa0ee81e27f9b0b765e0ad3b` | `--reveal commitment_receipt.json --content commitment_content.txt --salt 973598fa62a89efc9b89d3b07c6111e2af3813aa6d198a9ea6d899d9b87fe0bb --identity identity.json` | verified |
| `action_receipt.json` | `74a89522dee577a88db7d6b1db9c5ebfeb54ece826e5591e28d22a427d176a82` | `--action action_receipt.json --identity identity.json` | verified |
| `identity.json` | `2f799e66194cb43edde0f7768c891597637b9f2c475323c1dcdec0df8005b005` | the identity the others are pinned to | |
| `commitment_content.txt` | `6c302905234f9a70aa4b6c6456395b7cd0c11fd6726de1647f984d0b2a89d6aa` | the content the receipt's commitment opens to | |
| `../w11d/chain.json` | `3e00b6e2a876310482795a2f545104ba4f94a4e5135324af966786b9c5a25258` | `--chain chain.json --identity identity.json` | verified |

Each digest is of the file's bytes as committed. The JSON vectors end their lines with LF; a copy whose line ends
were rewritten is the same document and another digest. The content file's own line ends (CRLF) are part of what
its commitment is over, and the repository keeps every vector's bytes as committed on any checkout.

The worked examples below are checked by the project's tests against the second producer's functions and the
verifier's: canonical encodings (section 3), Merkle roots over the leaves shown as hexadecimal (section 6), one
commitment and one commitment root (section 5), and one block signature (section 8) made with the secret key of
RFC 8032's first test vector.

```json talima-examples
{
 "canonical": [
  {
   "value": {
    "b": 1,
    "a": [
     true,
     null
    ]
   },
   "encoding": "{\"a\":[true,null],\"b\":1}"
  },
  {
   "value": 0.30000000000000004,
   "encoding": "0.30000000000000004"
  },
  {
   "value": 1722276031.0966694,
   "encoding": "1722276031.0966694"
  },
  {
   "value": 5e-324,
   "encoding": "5e-324"
  },
  {
   "value": "é",
   "encoding": "\"\\u00e9\""
  },
  {
   "value": "\u007f/\"\\\n",
   "encoding": "\"\\u007f/\\\"\\\\\\n\""
  },
  {
   "value": "😀",
   "encoding": "\"\\ud83d\\ude00\""
  },
  {
   "value": 1,
   "encoding": "1"
  },
  {
   "value": 1.0,
   "encoding": "1.0"
  },
  {
   "value": 1700000000.0001,
   "encoding": "1700000000.0001"
  },
  {
   "value": 1e+16,
   "encoding": "1e+16"
  },
  {
   "value": 1000000000000000.0,
   "encoding": "1000000000000000.0"
  },
  {
   "value": 1e-05,
   "encoding": "1e-05"
  },
  {
   "value": 0.0001,
   "encoding": "0.0001"
  },
  {
   "value": -0.0,
   "encoding": "-0.0"
  },
  {
   "value": [],
   "encoding": "[]"
  },
  {
   "value": {},
   "encoding": "{}"
  },
  {
   "value": "Z",
   "encoding": "\"Z\""
  },
  {
   "value": {
    "Z": 1,
    "a": 2,
    "é": 3
   },
   "encoding": "{\"Z\":1,\"a\":2,\"\\u00e9\":3}"
  }
 ],
 "merkle": [
  {
   "leaves": [],
   "root": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
  },
  {
   "leaves": [
    "61"
   ],
   "root": "022a6979e6dab7aa5ae4c3e5e45f7e977112a7e63593820dbec1ec738a24f93c"
  },
  {
   "leaves": [
    "61",
    "62"
   ],
   "root": "b137985ff484fb600db93107c77b0365c80d78f5b429ded0fd97361d077999eb"
  },
  {
   "leaves": [
    "61",
    "62",
    "63"
   ],
   "root": "36642e73c2540ab121e3a6bf9545b0a24982cd830eb13d3cd19de3ce6c021ec1"
  },
  {
   "leaves": [
    "61",
    "62",
    "63",
    "64",
    "65"
   ],
   "root": "fe14a5426fbd70c0fa73f52342afed0da0bd23c4838662ccf6b88a3070ead97b"
  }
 ],
 "commitment": {
  "salt": "000102030405060708090a0b0c0d0e0f101112131415161718191a1b1c1d1e1f",
  "value": "example-model",
  "commitment": "65c373a7a2d6a5b08f11f42d5ca604bb47bcb11be6f04e89632033d46af958f0"
 },
 "commitment_root": {
  "commitments": {
   "model_id": "65c373a7a2d6a5b08f11f42d5ca604bb47bcb11be6f04e89632033d46af958f0",
   "ts": "d6683103923c87664518951015526e9536aa1c5f04ba95a955ccacc40f8c8c44"
  },
  "root": "23e9d59900009bdd7538dc12295a0886fed0ad9bcc5cc6869872bb4a0391a25d"
 },
 "signature": {
  "seed_rfc8032_test_1": "9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
  "public_key": "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a",
  "key_id": "21fe31dfa154a261626bf854046fd2271b7bed4b6abe45aa58877ef47f9721b9",
  "block_index": 7,
  "block_hash": "a20082bbf9c7c0603767978e85a30d82d18fb7bfcb97741fa857b48621e17652",
  "statement": "4e455855532d424c4f434b2d5631000000000000000007a20082bbf9c7c0603767978e85a30d82d18fb7bfcb97741fa857b48621e17652",
  "signature": "9e74d23cc93089c83af7cbfbd189915792bd31243e55d7a84313ed290aa03d89523dfe93cb308f7674a8510307eb086e9e91ab47db3529b6f28681164faa9809"
 }
}
```

`tests/fixtures/tb_golden/` also holds two proofs alone (`proof.json`, `proof_tombstoned.json`). A proof is not
a document a verifier is given by itself, so they are not vectors.

## 15. What verification does not show

A verified credential shows that the holder of the key named sealed a record whose disclosed fields hold exactly
the values shown, in a block that key signed. It does not show that the content is true; nor when the record was
sealed, until an anchor verifies; nor who held the key, unless the identity was pinned from a trusted source; nor
that the producer recorded everything it did; nor that the chain a document shows is the only one its key has
signed (two chains that part at some block each verify alone, and only someone holding both can see it). Each producer states its own coverage: what it records and what it
cannot see.

## Appendix A. Forms older than this document (informative)

The reference verifier also accepts forms the reference producer wrote before version 1's rules were fixed, and
one it still writes. *Layout 1*: a block whose hash covers its whole fingerprint; its proof carries the block's
body, and the leaf is the record hash alone. It appears in credentials only: a chain document holds layout-2
blocks, and the reference producer's begins at its first one. *A bare hash and a binding*: a record sealed
before it had a commitment root is a `lineage` leaf of 32 bytes, its record hash alone, in a layout-1 block or a
layout-2 one; a later block binds it to its root, and its proof carries that second inclusion. *A signature made
at issue*: section 8's `issue`, which the reference producer still writes for a block it appended unsigned.

Accepting the first two is the reference verifier's own choice and no part of conformance: a conforming producer
writes neither, no vector of section 14 holds one, and another verifier may refuse them. Where it accepts them it
holds these things of them and no others: a binding's block is layout 2; a binding's integers and byte strings
are typed and read as a proof's are, and a binding that names its record is held as a proof is (M-DOC-6); a
layout-1 block's sealed list is a list of lower-case hexadecimal (M-CAN-8). A layout-1 body is otherwise taken
as it is hashed: its other members are not typed, as a header's are (M-BLK-2).
