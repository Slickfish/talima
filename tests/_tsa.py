"""A test RFC 3161 time-stamping authority (W9 part 3b): real tokens, built here in DER and signed with RSA by
`cryptography`, so the suite exercises anchoring and `nexus-verify`'s check without a network. Not collected
(the name starts with an underscore). It answers exactly what a real authority answers to NEXUS's request: a
TimeStampResp whose token is a CMS SignedData over a TSTInfo carrying the request's 32-byte imprint.
"""
from __future__ import annotations

import datetime
import hashlib
import http.server
import secrets
import threading

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

OIDS = {"sha256": "2.16.840.1.101.3.4.2.1", "sha512": "2.16.840.1.101.3.4.2.3",
        "signedData": "1.2.840.113549.1.7.2", "tstInfo": "1.2.840.113549.1.9.16.1.4",
        "contentType": "1.2.840.113549.1.9.3", "messageDigest": "1.2.840.113549.1.9.4",
        "rsaEncryption": "1.2.840.113549.1.1.1", "sha256WithRSAEncryption": "1.2.840.113549.1.1.11",
        "policy": "1.3.6.1.4.1.58888.1", "ecdsa-with-SHA512": "1.2.840.10045.4.3.4"}


def _length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(raw)]) + raw


def tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _length(len(content)) + content


def seq(*items: bytes) -> bytes:
    return tlv(0x30, b"".join(items))


def set_of(*items: bytes) -> bytes:
    return tlv(0x31, b"".join(sorted(items)))


def integer(value: int) -> bytes:
    return tlv(0x02, value.to_bytes(max(1, (value.bit_length() + 8) // 8), "big", signed=True))


def oid(dotted: str) -> bytes:
    parts = [int(x) for x in dotted.split(".")]
    body = bytes([40 * parts[0] + parts[1]])
    for p in parts[2:]:
        out = [p & 0x7F]
        p >>= 7
        while p:
            out.append(0x80 | (p & 0x7F))
            p >>= 7
        body += bytes(reversed(out))
    return tlv(0x06, body)


def _read(data: bytes, pos: int):
    tag, length = data[pos], data[pos + 1]
    pos += 2
    if length & 0x80:
        n = length & 0x7F
        length, pos = int.from_bytes(data[pos:pos + n], "big"), pos + n
    return tag, data[pos:pos + length], pos + length


def _children(content: bytes):
    out, pos = [], 0
    while pos < len(content):
        tag, body, pos = _read(content, pos)
        out.append((tag, body))
    return out


def parse_request(der: bytes) -> tuple[bytes, int | None]:
    """The imprint and nonce of a TimeStampReq (version, messageImprint, [policy], [nonce], [certReq])."""
    _tag, body, _ = _read(der, 0)
    fields = _children(body)
    imprint = _children(fields[1][1])[1][1]
    nonce = next((int.from_bytes(b, "big") for t, b in fields[2:] if t == 0x02), None)
    return imprint, nonce


class StampAuthority:
    """One key and its self-signed certificate (extended key usage: time stamping): RSA, or, as FreeTSA's is,
    ECDSA on P-384 signing with SHA-512 (`kind="ec"`)."""

    def __init__(self, *, key_size: int = 2048, digest: str = "sha256", kind: str = "rsa"):
        self.kind = kind
        if kind == "ec":
            self.key, digest = ec.generate_private_key(ec.SECP384R1()), "sha512"
        else:
            self.key = rsa.generate_private_key(public_exponent=65537, key_size=key_size)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "NEXUS test time-stamping authority")])
        now = datetime.datetime.now(datetime.timezone.utc)
        self.cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                     .public_key(self.key.public_key()).serial_number(x509.random_serial_number())
                     .not_valid_before(now - datetime.timedelta(days=1)).not_valid_after(now + datetime.timedelta(days=30))
                     .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
                     .sign(self.key, hashes.SHA256()))
        self.cert_pem = self.cert.public_bytes(serialization.Encoding.PEM)
        self.digest = digest
        self.requests: list[bytes] = []

    def token(self, imprint: bytes, *, nonce: int | None = None, at: datetime.datetime | None = None,
              status: int = 0) -> bytes:
        """A TimeStampResp for `imprint` (the 32 bytes NEXUS sends)."""
        if status not in (0, 1):
            return seq(seq(integer(status)))
        at = at or datetime.datetime.now(datetime.timezone.utc)
        tst = seq(integer(1), oid(OIDS["policy"]),
                  seq(seq(oid(OIDS["sha256"]), b"\x05\x00"), tlv(0x04, imprint)),
                  integer(int.from_bytes(secrets.token_bytes(8), "big") >> 1), tlv(0x18, at.strftime("%Y%m%d%H%M%SZ").encode("ascii")),
                  *([integer(nonce)] if nonce is not None else []))
        h = hashlib.new(self.digest)
        h.update(tst)
        attrs = [seq(oid(OIDS["contentType"]), set_of(oid(OIDS["tstInfo"]))),
                 seq(oid(OIDS["messageDigest"]), set_of(tlv(0x04, h.digest())))]
        attrs_content = b"".join(sorted(attrs))
        algorithm = hashes.SHA256() if self.digest == "sha256" else hashes.SHA512()
        if self.kind == "ec":
            signature = self.key.sign(tlv(0x31, attrs_content), ec.ECDSA(algorithm))
            signature_algorithm = seq(oid(OIDS["ecdsa-with-SHA512"]))
        else:
            signature = self.key.sign(tlv(0x31, attrs_content), padding.PKCS1v15(), algorithm)
            signature_algorithm = seq(oid(OIDS["rsaEncryption"]), b"\x05\x00")
        issuer = self.cert.issuer.public_bytes()
        signer = seq(integer(1), seq(issuer, integer(self.cert.serial_number)),
                     seq(oid(OIDS[self.digest]), b"\x05\x00"), tlv(0xA0, attrs_content),
                     signature_algorithm, tlv(0x04, signature))
        signed_data = seq(integer(3), set_of(seq(oid(OIDS[self.digest]), b"\x05\x00")),
                          seq(oid(OIDS["tstInfo"]), tlv(0xA0, tlv(0x04, tst))),
                          tlv(0xA0, self.cert.public_bytes(serialization.Encoding.DER)), set_of(signer))
        return seq(seq(integer(status)), seq(oid(OIDS["signedData"]), tlv(0xA0, signed_data)))

    def serve(self):
        """An HTTP server on loopback that answers `application/timestamp-query` POSTs. Returns (url, stop)."""
        authority = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                authority.requests.append(body)
                imprint, nonce = parse_request(body)
                reply = authority.token(imprint, nonce=nonce)
                self.send_response(200)
                self.send_header("Content-Type", "application/timestamp-reply")
                self.send_header("Content-Length", str(len(reply)))
                self.end_headers()
                self.wfile.write(reply)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=10)
        return f"http://127.0.0.1:{server.server_address[1]}/tsr", stop
