"""MCP-2 — NEXUS as an MCP server.

One stdio process per person over their own store; the record is the
product. The turn is split at the model call (`backend/loop/agentic_loop.py`:
`prepare_turn` / `complete_turn`): `nexus_retrieve` runs the first half and
hands back the block and a `retrieval_id`; the client's model answers;
`nexus_record` runs the second half on the answer the client sent and seals
the record with `record_scope = mcp_block`. The HTTP app is never
constructed here and the bearer token has no role (an AST guard holds both).

Importing this package does nothing: no file, no socket, no environment
read. `python -m backend.mcp` is the entry point.
"""
