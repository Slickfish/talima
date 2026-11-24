"""W10b (run/specs/W10b.md D10, D11) — HTTP Message Signatures (RFC 9421) over NEXUS's own outbound requests.

With `NEXUS_SIGN_REQUESTS=on` every runtime action's request is signed with the instance's Ed25519 key (W9)
before it leaves: the signature covers the request's method, authority, path and query (`@method`, `@authority`,
`@path`, `@query`), with `created`, `expires`, `keyid` (the instance key's id, listed at `GET /identity`) and
`alg="ed25519"`, and it is sent as `Signature-Input` and `Signature` under the label `nexus`. The signature rides
the action's record, so whoever received the request can ask NEXUS for the sealed authorisation behind it
(`GET /identity/signed-action?signature=`) and check it offline.

`off` is the default and sends no signature header. An unknown value reads `off`, declared on `/cortex/stats`.

Interoperability is held to RFC 9421's own Ed25519 vector (Appendix B.2.6; its key from B.1.4), by test, offline.
A published verifier (`http-message-signatures` 3.0.0, from PyPI) accepted a request signed here and refused it
tampered, in the run's interop check (RUN_LOG, W10b): a record, not a test.
"""
from __future__ import annotations

import base64
import os
from urllib.parse import urlsplit

SETTING = "NEXUS_SIGN_REQUESTS"
MODES = ("off", "on")
LABEL = "nexus"
COVERED = ("@method", "@authority", "@path", "@query")
VALIDITY_SECONDS = 300
ALG = "ed25519"
_DEFAULT_PORTS = {"http": 80, "https": 443}


def mode() -> str:
    """The setting, read at call time; an unknown value is `off`."""
    raw = (os.environ.get(SETTING) or "off").strip().lower()
    return raw if raw in MODES else "off"


def declaration() -> dict:
    raw = (os.environ.get(SETTING) or "off").strip().lower()
    return {"mode": mode(), "setting": raw, "declared": None if raw in MODES else f"unknown {SETTING}={raw!r}: read as off",
            "covered": list(COVERED), "label": LABEL, "alg": ALG}


def component_value(name: str, *, method: str, url: str, headers: dict) -> str:
    """One component's value (RFC 9421 §2.1, §2.2): a derived component, or a header field by its
    lower-cased name with its values joined by ", "."""
    parts = urlsplit(url)
    if name == "@method":
        return method.upper()
    if name == "@authority":
        host = (parts.hostname or "").lower()
        port = parts.port
        return host if port is None or port == _DEFAULT_PORTS.get(parts.scheme) else f"{host}:{port}"
    if name == "@path":
        return parts.path or "/"
    if name == "@query":
        return "?" + parts.query
    if name == "@target-uri":
        return url
    if name.startswith("@"):
        raise ValueError(f"an unsupported derived component {name!r}")
    values = [str(v).strip() for k, v in (headers or {}).items() if str(k).lower() == name]
    if not values:
        raise ValueError(f"the request carries no {name!r} header to cover")
    return ", ".join(values)


def signature_params(covered, *, created: int, keyid: str, expires: "int | None" = None,
                     alg: "str | None" = None) -> str:
    """The `@signature-params` value (RFC 9421 §2.3), in the order RFC 9421's own examples use."""
    inner = " ".join(f'"{c}"' for c in covered)
    out = f"({inner});created={int(created)}"
    if expires is not None:
        out += f";expires={int(expires)}"
    out += f';keyid="{keyid}"'
    if alg is not None:
        out += f';alg="{alg}"'
    return out


def signature_base(covered, params: str, *, method: str, url: str, headers: dict) -> bytes:
    """The signature base (RFC 9421 §2.5): one line per covered component, then `@signature-params`."""
    lines = [f'"{c}": {component_value(c, method=method, url=url, headers=headers)}' for c in covered]
    lines.append(f'"@signature-params": {params}')
    return "\n".join(lines).encode("utf-8")


def sign_request(key, *, method: str, url: str, headers: dict, now: float) -> dict:
    """`{"Signature-Input": ..., "Signature": ..., "signature": <base64>, "key_id": ...}` for one request,
    signed with `key` (an `instance_key.InstanceKey`)."""
    created = int(now)
    params = signature_params(COVERED, created=created, expires=created + VALIDITY_SECONDS, keyid=key.key_id,
                              alg=ALG)
    value = base64.b64encode(key.sign(signature_base(COVERED, params, method=method, url=url,
                                                     headers=headers))).decode("ascii")
    return {"Signature-Input": f"{LABEL}={params}", "Signature": f"{LABEL}=:{value}:", "signature": value,
            "key_id": key.key_id}


def signer_for(key_path, chain):
    """The recorder's signer: the instance's key at `key_path`, read at the first signature (W9's rule)."""
    def sign(*, method: str, url: str, headers: dict, now: float) -> dict:
        from backend.memory import instance_key as _ik
        return sign_request(_ik.load_or_create(key_path, chain=chain), method=method, url=url, headers=headers,
                            now=now)
    return sign


def parse_signature_input(header: str) -> tuple[str, tuple, str]:
    """(label, covered components, the params string) from one `Signature-Input` member."""
    label, _, params = header.partition("=")
    inner = params[params.index("(") + 1:params.index(")")]
    covered = tuple(part.strip('"') for part in inner.split()) if inner.strip() else ()
    return label.strip(), covered, params.strip()


def verify_request(public_key: bytes, *, method: str, url: str, headers: dict) -> bool:
    """True when the request's `Signature` verifies under `public_key` over the base its `Signature-Input`
    names: what the request's recipient checks, here with `cryptography` (the instance's own verify)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    lowered = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
    label, covered, params = parse_signature_input(lowered["signature-input"])
    sig_label, _, value = lowered["signature"].partition("=")
    if sig_label.strip() != label:
        return False
    signature = base64.b64decode(value.strip().strip(":"))
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(
            signature, signature_base(covered, params, method=method, url=url, headers=headers))
        return True
    except InvalidSignature:
        return False
