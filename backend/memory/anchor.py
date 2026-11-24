"""W9 (run/specs/W9.md D7; the operator's decision 3) — dating blocks with an RFC 3161 authority.

An instance's signature proves who sealed a block, never when: a priority
claim (what was known on a date) needs a time a stranger trusts. Anchoring
sends a block's hash, and nothing else, to a time-stamping authority, keeps
the authority's signed receipt beside the block, and lets a credential carry
it for `nexus-verify --tsa-cert` to check offline.

`NEXUS_ANCHOR` is `off` (the default: nothing leaves the machine) or
`rfc3161`, with `NEXUS_ANCHOR_URL` naming any RFC 3161 authority (FreeTSA's is
`https://freetsa.org/tsr`). What leaves is the 32-byte block hash as the
request's SHA-256 imprint, a random nonce and the request flags: the block's
header is what that hash is the digest of, and the header holds no content of
any record. Anchoring runs on the KAIROS idle cycle, never on a turn; a block
the authority does not stamp is counted and tried again on a later cycle, and
nothing else in the cycle waits on it.
"""
from __future__ import annotations

import base64
import os
import secrets
import time

ANCHOR_SETTING = "NEXUS_ANCHOR"
URL_SETTING = "NEXUS_ANCHOR_URL"
MODES = ("off", "rfc3161")
ANCHOR_BATCH = 10            # blocks a cycle asks to be stamped, at most: PROVISIONAL, fitted to nothing
TIMEOUT_SECONDS = 15.0
_SHA256 = "2.16.840.1.101.3.4.2.1"


def anchor_mode() -> str:
    """The setting, read at call time; an unknown value is `off`, declared on /cortex/stats."""
    raw = (os.environ.get(ANCHOR_SETTING) or "off").strip().lower()
    return raw if raw in MODES else "off"


def anchor_url() -> str:
    return (os.environ.get(URL_SETTING) or "").strip()


# ---------- DER, as little as the request and the response's first check need ----------

def _tlv(tag: int, content: bytes) -> bytes:
    n = len(content)
    length = bytes([n]) if n < 0x80 else bytes([0x80 | ((n.bit_length() + 7) // 8)]) + n.to_bytes(
        (n.bit_length() + 7) // 8, "big")
    return bytes([tag]) + length + content


def _oid(dotted: str) -> bytes:
    parts = [int(x) for x in dotted.split(".")]
    body = bytes([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        out = [p & 0x7F]
        p >>= 7
        while p:
            out.append(0x80 | (p & 0x7F))
            p >>= 7
        body += bytes(reversed(out))
    return _tlv(0x06, body)


def build_request(block_hash: str, nonce: int) -> bytes:
    """A TimeStampReq (RFC 3161 §2.4.1): version 1, the block hash as a SHA-256
    imprint, a nonce, and a request for the authority's certificate."""
    imprint = bytes.fromhex(block_hash)
    if len(imprint) != 32:
        raise ValueError("a block hash is 32 bytes")
    nonce_bytes = nonce.to_bytes(max(1, (nonce.bit_length() + 8) // 8), "big", signed=True)
    return _tlv(0x30, _tlv(0x02, b"\x01")
                + _tlv(0x30, _tlv(0x30, _oid(_SHA256) + b"\x05\x00") + _tlv(0x04, imprint))
                + _tlv(0x02, nonce_bytes) + _tlv(0x01, b"\xff"))


def _read(data: bytes, pos: int):
    tag, length = data[pos], data[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7F
        if n == 0 or n > 4:
            raise ValueError("an indefinite or oversized length")
        length, pos = int.from_bytes(data[pos:pos + n], "big"), pos + n
    if pos + length > len(data):
        raise ValueError("a truncated element")
    return tag, data[pos:pos + length], pos + length


def _items(content: bytes) -> list:
    out, pos = [], 0
    while pos < len(content):
        tag, body, pos = _read(content, pos)
        out.append((tag, body))
    return out


def response_problem(der: bytes, block_hash: str, nonce: int) -> str | None:
    """None when the response grants a stamp over this block's hash with this
    nonce; otherwise why not. The signature is `nexus-verify`'s to check,
    against the authority's certificate a verifier trusts."""
    try:
        _t, body, _ = _read(der, 0)
        parts = _items(body)
        status = int.from_bytes(_items(parts[0][1])[0][1], "big")
        if status not in (0, 1) or len(parts) < 2:
            return f"not granted (status {status})"
        signed = _items(_read(_items(parts[1][1])[1][1], 0)[1])
        encap = _items(signed[2][1])
        tst = _items(_read(_read(encap[1][1], 0)[1], 0)[1])
        if _items(tst[2][1])[1][1] != bytes.fromhex(block_hash):
            return "the stamp's imprint is not the block's hash"
        nonces = [b for t, b in tst[5:] if t == 0x02]
        if not nonces:
            return "the stamp carries no nonce, and the request sent one (RFC 3161 §2.4.2)"
        if int.from_bytes(nonces[0], "big", signed=True) != nonce:
            return "the stamp's nonce is not the request's"
        return None
    except (IndexError, ValueError) as e:
        return f"the response cannot be read ({type(e).__name__})"


async def stamp(block_hash: str, url: str, *, client=None) -> tuple[str | None, str | None]:
    """(the receipt, base64, or None; why not, or None)."""
    import httpx
    nonce = int.from_bytes(secrets.token_bytes(8), "big") >> 1     # 63 random bits, never a seed
    request = build_request(block_hash, nonce)
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        resp = await client.post(url, content=request, headers={"Content-Type": "application/timestamp-query"})
        if resp.status_code != 200:
            return None, f"the authority answered HTTP {resp.status_code}"
        why = response_problem(resp.content, block_hash, nonce)
        return (None, why) if why else (base64.b64encode(resp.content).decode("ascii"), None)
    except Exception as e:                      # a network failure is counted, never raised into the cycle
        return None, f"{type(e).__name__}: {e}"
    finally:
        if own:
            await client.aclose()


async def anchor_pending(chain, *, url: str | None = None, now: float | None = None, client=None) -> dict:
    """Stamp the signed blocks no receipt covers yet, oldest first, at most
    ANCHOR_BATCH. Stops at the first failure: the authority is probably down,
    and the next cycle tries again."""
    url = url or anchor_url()
    out = {"anchored": 0, "failed": 0, "error": None}
    if not url:
        out["error"] = f"{URL_SETTING} is unset"
        return out
    for block in chain.unanchored_signed(limit=ANCHOR_BATCH):
        token, why = await stamp(block.block_hash, url, client=client)
        if token is None:
            out["failed"] += 1
            out["error"] = why
            break
        chain.store_anchor(block.index, method="rfc3161", authority=url, token=token,
                           at=time.time() if now is None else now)
        out["anchored"] += 1
    return out
