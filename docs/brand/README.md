# The Talima marks

Three drawings of the Talima mark, as the operator supplied them, without the provenance manifest they were
delivered with (below). Each is a 64 × 64 view box: scale it, do
not redraw it.

| File | What it shows | sha256 |
|---|---|---|
| `talima-mark.svg` | The open ring: the record | `752a0c0d14f86ca3d7d949adca8ba63024aea9eea4158ba0bfa873f7ea081c24` |
| `talima-seal.svg` | The disc: the sealed state | `67f044b1c694952556ad0a9010c48aae3323a81640e78edde2851f3c16f1401e` |
| `talima-mark-small.svg` | The small drawing, for 24 px and under | `dbb6692ed9bde47214810cb970e72fea46e6106c46704a0a86d885286a7f5cc2` |

Each file was delivered with a C2PA provenance manifest, in a `<metadata>` element of 7,736 bytes, and the
declaration of its namespace on the `<svg>` element. Both are removed for publication (the operator's decision, 20
November 2026); every other byte is as supplied, so the drawings are unchanged. An SVG optimiser that rewrites a
file changes its sha256 above.

The marks name the standard and its verifier (`talima-verify`). NEXUS, the reference engine, keeps its own name, and
its console is not rebranded.
