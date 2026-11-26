# The Talima marks

Six drawings of the Talima mark, as the operator supplied them, without the provenance manifest each was
delivered with (below). Each is a 64 × 64 view box: scale it, do
not redraw it.

| File | What it shows | sha256 |
|---|---|---|
| `talima-mark.svg` | The open ring: the record | `752a0c0d14f86ca3d7d949adca8ba63024aea9eea4158ba0bfa873f7ea081c24` |
| `talima-seal.svg` | The disc: the sealed state | `67f044b1c694952556ad0a9010c48aae3323a81640e78edde2851f3c16f1401e` |
| `talima-mark-small.svg` | The small drawing, for 24 px and under | `dbb6692ed9bde47214810cb970e72fea46e6106c46704a0a86d885286a7f5cc2` |
| `talima-mark-clay.svg` | The open ring in clay (`#C2410C`): the README's | `e7285eb8ca9e0ed1da6bb1e94b243659d8e4eb1d407158366f344743ca3d4686` |
| `talima-mark-ink.svg` | The open ring in ink (`#17191E`), for a light ground | `4d74908db24559988a05166b1b4a800a2a4c0cc2e81477c8c555bef56ac85d42` |
| `talima-mark-paper.svg` | The open ring in paper (`#F3F1EC`), for a dark ground | `4d1fb5bd56c6e9717c7a4cb9bddf058df25653cc795f300635dae72aab9cf52a` |

The first three draw in the colour of the text around them (`currentColor`). A page that shows an SVG as an image,
as a README does, gives it no such colour, so the last three carry their own, and a size (128 × 128) where no
width is given.

Each file was delivered with a C2PA provenance manifest, in a `<metadata>` element of 7,736 bytes, and the
declaration of its namespace on the `<svg>` element. Both are removed for publication (the operator's decisions, 20
November 2026 for the first three and 26 November 2026 for the three colours); every other byte is as supplied, so
the drawings are unchanged. An SVG optimiser that rewrites a file changes its sha256 above.

The marks name the standard and its verifier (`talima-verify`). NEXUS, the reference engine, keeps its own name, and
its console is not rebranded.
