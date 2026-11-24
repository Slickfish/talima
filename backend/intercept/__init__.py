"""W10c — the interceptor: a standalone Talima recorder between an agent harness and its model provider.

A harness is pointed at this process as its model endpoint; every exchange passes through on its way to the
provider and is recorded: the request before it is sent, the response when it is complete, both sealed into a
chain a stranger can verify. It runs with no memory engine: the record, the seal, the chain, the store and the lock
are all it loads (tests/test_w10c_standalone.py holds that from the import graph).

Importing this package does nothing: no file, no socket, no environment read. `python -m backend.intercept` (the
released binary `talima-intercept`) is the entry point; `docs/INTERCEPTOR.md` is the guide.
"""
