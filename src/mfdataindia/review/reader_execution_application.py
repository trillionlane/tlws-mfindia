"""Real selected-profile ASGI checks, not live internal-consumer acceptance."""
import asyncio
from dataclasses import dataclass
import hashlib
import json
import time

from cryptography.hazmat.primitives import serialization

from .app import create_review_app
from .auth import AssertionVerifier
from . import broad_reader_direct_delivery_spec as spec
from . import broad_reader_direct_grant as direct
from . import reader_access_profile as profiles
from .reader_execution_contract import require, EXECUTION_LIMITATIONS
from .policy import fingerprint

FLAGS = dict(calendar=True, rolling=True, current_facts=True, fact_verification=True,
             enrichment=True, official_facts=True, portfolios=True, overlap=True,
             distributions=True, idcw_catalog=True, monthly_facts=True, fact_refresh=True,
             multi_amc=True, amc_books=True)
ROUTES = {"monthly": "/api/enrichment/v4/funds/{code}/facts",
          "refresh": "/api/enrichment/v5/status", "multi": "/api/enrichment/v8/funds/{code}/facts",
          "book": "/api/enrichment/v9/funds/{code}/facts"}
MAX_RESPONSE_BYTES = 1024 * 1024


def public_trust(verifier):
    require(type(verifier) is AssertionVerifier, "APPLICATION_TRUST_CONTEXT_REQUIRED")
    return {"public_key": verifier.key.public_bytes(serialization.Encoding.Raw,
                serialization.PublicFormat.Raw).hex(), "issuer": verifier.issuer,
            "audience": verifier.audience, "caller": verifier.caller,
            "allowed_scopes": sorted(verifier.allowed_scopes)}


async def _request(app, method, path, *, token=None, body=b"", extra=()):
    status = None
    headers = []
    length = 0
    received = False
    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()
    async def send(message):
        nonlocal status, headers, length
        if message["type"] == "http.response.start":
            require(status is None, "APPLICATION_RESPONSE_PROTOCOL_INVALID")
            status, headers = message["status"], message.get("headers", [])
        elif message["type"] == "http.response.body":
            length += len(message.get("body", b""))
            require(length <= MAX_RESPONSE_BYTES, "APPLICATION_RESPONSE_BOUND_EXCEEDED")
            # Deliberately discard business values and response bytes.
    request_headers = [(b"host", b"internal-reader-check.invalid"), *extra]
    if token is not None:
        request_headers.append((b"authorization", b"Bearer " + token.encode("ascii")))
    if body:
        request_headers += [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]
    started = time.monotonic()
    await app({"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
               "http_version": "1.1", "method": method, "scheme": "https", "path": path,
               "raw_path": path.encode(), "query_string": b"", "root_path": "",
               "headers": request_headers, "client": ("127.0.0.1", 0),
               "server": ("internal-reader-check.invalid", 443)}, receive, send)
    no_store = any(key.lower() == b"cache-control" and value.lower() == b"no-store"
                   for key, value in headers)
    return {"status": status, "no_store": no_store,
            "seconds": round(time.monotonic() - started, 6)}


@dataclass(frozen=True)
class ApplicationCheck:
    dsn: str
    verifier: AssertionVerifier
    read_assertion: str
    admin_assertion: str | None = None

    def validate(self, plan):
        spec.connection_arguments("reader", self.dsn)  # Parse only; never connects.
        require(fingerprint(public_trust(self.verifier)) == plan["application_trust_sha256"],
                "APPLICATION_TRUST_BINDING_MISMATCH")
        for token, expected in ((self.read_assertion, {"review:read"}),
                                (self.admin_assertion, {"review:read", "review:admin"})):
            require(type(token) is str and 0 < len(token) <= 8192, "APPLICATION_ASSERTION_REQUIRED")
            principal = self.verifier.verify(token)
            require(principal.scopes == expected, "APPLICATION_ASSERTION_SCOPE_MISMATCH")
        return {"trust_sha256": fingerprint(public_trust(self.verifier)),
                "caller_sha256": hashlib.sha256(self.verifier.caller.encode()).hexdigest(),
                "fresh_database_checks": "UNVERIFIED"}

    async def check_async(self, plan):
        self.validate(plan)
        direct.direct_live(plan["window"], remaining=15)
        app = create_review_app(self.dsn, verifier=self.verifier,
                                 reader_profile=profiles.BROAD, **FLAGS)
        records = []
        timer = time.monotonic()
        async with app.router.lifespan_context(app):
            direct.direct_live(plan["window"])
            startup = round(time.monotonic() - timer, 6)
            schema = app.openapi()
            for name, template in ROUTES.items():
                direct.direct_live(plan["window"])
                code = plan["probe_codes"].get(name)
                path = template if name == "refresh" else template.format(code=code)
                unauthenticated = await _request(app, "GET", path)
                require(unauthenticated["status"] == 401 and unauthenticated["no_store"],
                        "UNAUTHENTICATED_READ_BOUNDARY_FAILED")
                authenticated = await _request(app, "GET", path, token=self.read_assertion)
                direct.direct_live(plan["window"])
                require(authenticated["status"] == 200 and authenticated["no_store"],
                        "AUTHENTICATED_APPLICATION_READ_FAILED")
                records.append({"route": name, "unauthenticated": unauthenticated,
                                "authenticated": authenticated,
                                "response_schema_sha256": fingerprint(schema["paths"][template]["get"]["responses"]["200"]),
                                "request_sha256": fingerprint({"method": "GET", "path": path,
                                    "assertion_sha256": hashlib.sha256(self.read_assertion.encode()).hexdigest()})})
            mutation = json.dumps({"expected_version": 0, "expected_input_hash": "a" * 64,
                "result_id": "b" * 64, "status": "reviewed", "disposition": "consistent_with_evidence",
                "reason": "synthetic-access-check", "evidence": ["fixture://access-only"]}).encode()
            scopes = []
            for scope, token in (("read", self.read_assertion), ("read_admin", self.admin_assertion)):
                direct.direct_live(plan["window"])
                denied = await _request(app, "POST", "/api/review/v1/funds/1/reviews", token=token,
                    body=mutation, extra=((b"idempotency-key", b"ro02-denied-mutation"),
                                          (b"x-reader-profile", profiles.RESTRICTED.encode())))
                require(denied["status"] == 403 and denied["no_store"], "APPLICATION_MUTATION_BOUNDARY_FAILED")
                scopes.append({"scope": scope, **denied})
            direct.direct_live(plan["window"])
        return {"format": "mf-reader-application-check-v1",
                "execution_limitations": list(EXECUTION_LIMITATIONS),
                "state": "APPLICATION_CHECKS_VERIFIED_CONSUMER_PENDING",
                "surface": "IN_PROCESS_SELECTED_PROFILE_APPLICATION", "profile": profiles.BROAD,
                "trust_sha256": plan["application_trust_sha256"], "startup_seconds": startup,
                "routes": records, "denied_mutation_scopes": scopes,
                "consumer_acceptance": "PENDING_RO04"}

    def check(self, plan):
        return asyncio.run(self.check_async(plan))
