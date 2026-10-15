# Power Cell server protocol

The agent extension is implemented in migration `0006`. Run `throng-api migrate`
before starting an updated API/controller. SQLite and PostgreSQL use the existing
serialized write transaction for scheduling, capacity, cleanup and replay guards.
Older generic agents keep their existing bearer/session protocol. The Power Cell
runtime cannot be created using the legacy `/computers` endpoint.

## Deployment

Configure all four values together:

```dotenv
THRONG_POWERCELL_ACME_URL=https://example.org/acme/powercell
THRONG_POWERCELL_CA_CERTIFICATE=/run/secrets/powercell-ca.pem
THRONG_POWERCELL_CA_KEY=/run/secrets/powercell-ca-key.pem
THRONG_POWERCELL_SECRET=<at least 32 random characters, persistent across restarts>
```

The certificate must be an EC CA with its matching unencrypted PEM private key.
Provision and protect this issuer separately; the application never silently
creates a development CA. The HTTPS endpoint must be reachable by macOS and its
TLS certificate must be trusted by that Mac. Route `/acme/powercell/*` to this
application, preserving paths. A URL prefix stripped by a reverse proxy may be
included in the configured public URL. Do not log directory secrets, signed
bodies, credentials or issuer key material. Directory capabilities expire with
the enrollment (one hour by default).

`THRONG_POWERCELL_SECRET` encrypts the credential recovery material needed after
an uncertain enrollment response. Back it up with the database and CA keys.
Changing it makes unfinished credential recovery unavailable; plan rotation
rather than generating a new value every startup. Agent credentials themselves
remain hashed in `agent_credentials`.

Without these settings the API still serves generic workers and all execution
extensions, but does not advertise `powercell.enrollment.v1` and refuses to enroll
Power Cell devices. Partial or invalid issuer configuration fails startup.

For the native macOS agent, also configure the authenticated network relay:

```dotenv
THRONG_POWERCELL_RELAY_URL=https://relay.example.org:8444
THRONG_POWERCELL_RELAY_CERTIFICATE=/run/secrets/relay-fullchain.pem
THRONG_POWERCELL_RELAY_KEY=/run/secrets/relay-key.pem
THRONG_POWERCELL_RELAY_HOST=0.0.0.0
```

The URL must be an HTTPS origin (no credentials, path, query or fragment). Its
port is the listener port; the bind address defaults to `127.0.0.1`. Use a TLS
certificate trusted by macOS, and expose this listener directly or through TCP
passthrough. Ordinary HTTP reverse proxies must not terminate its TLS: the API
must verify the actual client certificate. Run one API process per relay address
and port. TLS 1.3, HTTP/2 and a client certificate issued by the Power Cell CA are
mandatory; partial configuration or unusable TLS material fails startup.
`deploy/run.py --env-file <path>` keeps that file selected in its child services;
standalone processes can select it with `THRONG_ENV_FILE=<path>`. An explicitly
selected file does not inherit settings from a different local `.env`.

The relay accepts TCP CONNECT only to its own virtual API destination. It does
not proxy arbitrary hosts. Each connection's verified TLS certificate is attached
to the API request internally; no public certificate/fingerprint header is trusted.
There is no additional loopback HTTP listener accepting trusted identity headers.

Additional limits: `THRONG_ENROLLMENT_SECONDS` (3600),
`THRONG_DEVICE_CERTIFICATE_DAYS` (365), `THRONG_DEVICE_PROOF_SECONDS` (300),
`THRONG_AGENT_MUTATION_SECONDS` (86400), `THRONG_MAX_DIAGNOSTIC_BYTES` (8 MiB),
`THRONG_MAX_COMPUTER_DIAGNOSTICS_BYTES` (64 MiB),
`THRONG_DIAGNOSTIC_RETENTION_DAYS` (30). Expired protocol nonces, cleanup retry
records and diagnostics are removed by the controller. Diagnostic reads enforce
expiry even when the controller is stopped.

## Discovery and enrollment

Public `GET /v1/agent-capabilities` returns `protocol_version: 1`, runtime
`powercell-ubuntu-arm64-v1`, image list `["ubuntu:24.04"]`,
`attestation_required: true`, and supported extensions:
`powercell.enrollment.v1`, `powercell.capacity.v1`, `powercell.eviction.v1`,
`powercell.diagnostics.v1`, `powercell.isolation.v1`.
When the relay is configured, discovery also advertises `powercell.relay.v1`.

With a personal `manage` token, POST `/v1/agent-enrollments` and
`Idempotency-Key`:

```json
{"installation_id":"uuid","name":"My Mac","company_id":null,"os":"linux","architecture":"arm64","runtime":"powercell-ubuntu-arm64-v1","isolation_modes":["vm","shared"],"cpu_millis":4000,"memory_bytes":4294967296,"disk_bytes":8589934592}
```

Company enrollment requires its administrator role, rechecked when finishing.
Responses and GET `/v1/agent-enrollments/{id}` contain
`id,status,expires_at,certificate_sha256,relay`. Status is `pending_attestation`,
`verified` or `expired`. The certificate hash is absent until a matching CSR has
been signed after successful attestation. Only the initiating user can read or
finish an enrollment. At most 20 live enrollments per initiating user are allowed.

GET `.../{id}/profile` returns `application/x-apple-aspen-config`. It contains a
ACME identity payload, no MDM/root/restriction payloads.
It requests hardware-bound, nonextractable P-256 with `Attest=true`,
`AllowAllAppsAccess=false`, digital-signature usage and client-auth EKU.

With a relay configured, the profile also contains `com.apple.relay.managed`.
It uses `PayloadScope=System`: the built-in relay requires its hardware-bound
ACME identity in the system data protection keychain. Installation requires
manual administrator consent. Without a relay, the profile remains user scoped.
It routes only its own virtual `<enrollment>.powercell-<hash>.invalid` hostname and refers
to the **same ACME payload UUID** for client authentication. macOS uses the
nonextractable attested key itself; the app neither exports that key nor needs
access to the system-only identity. Other computer traffic is unaffected.
The `relay` response field contains `url` and `api_origin`; without this feature
it is null. The agent keeps the original server URL as its credential origin and
rewrites device request URLs to `api_origin`, preserving paths, queries and bodies.
The virtual hostname cannot resolve to a public fallback without the profile.
Routes differ between enrollment attempts, so a previously installed profile
cannot select the wrong identity for a new connection or another account.

After installation, authenticated GET `.../{id}/verification` through the relay
checks the enrollment owner's token and possession of its exact certificate. It
returns the enrollment status, or 409 for a pending/expired unbound attempt and
401 for an invalid device proof. The agent probes this endpoint before finishing.

The ACME service implements newNonce, newAccount, newOrder, POST-as-GET polling,
`device-attest-01`, CSR finalization and PEM chain retrieval. Each enrollment has
one account/order/certificate. ES256 and RS256 account signatures are supported;
the attested identity/CSR must use P-256. JWS URL, account, algorithm and nonce are
checked. A finalized order cannot change key or CSR. Requests cannot obtain DNS
certificates or arbitrary CSR extensions.

Apple attestation verification uses OpenSSL path validation against the bundled,
SHA-256-pinned Apple Enterprise Attestation Root, validity intervals, challenge
freshness, nonempty certified serial/UDID and Mac/Secure Enclave properties. The
freshness extension must contain **SHA256(challenge token)**, as documented by
Apple. The issued CSR must prove possession of the exact attested public key.
No client UUID, serial, self-signed key or forwarded proxy header substitutes for
attestation. Certificate SAN/subject and EKU are generated by the server.

POST `.../{id}/finish` with an empty JSON object, personal `manage` token,
`Idempotency-Key` and a device proof returns:

```json
{"computer_id":"uuid","credential":"agt_...","device_id":"uuid","certificate_sha256":"hex","relay":{"url":"https://relay.example.org:8444","api_origin":"http://<enrollment>.powercell-<hash>.invalid"}}
```

An exact retry using a new proof nonce recovers the same credential. A different
finish key, rotated/revoked credential or revoked certificate cannot recover it.
Physical identity is SHA-256 of a domain-separated Apple-certified UDID. Different
installations and accounts can bind the same physical device; deleting a binding
preserves its billing identity. Enrollment IDs, installation IDs and physical
IDs are separate records.

On the relay transport, the TLS certificate provides the device proof. Each API
request rechecks its exact enrollment/computer binding, validity interval and
revocation, including on an already-established TLS connection. Bearer tokens,
organization authorization, sessions and mutation idempotency still apply.
Registration and stable physical-device verification do not impose license or
computer-count limits.

## Request signatures

Every endpoint under `/agent/v1` requires a possession proof for enrolled Power
Cell computers, including session discovery, session creation and late uploads.
The server rechecks credential binding/revocation in the handler after proof
verification. Rotating a bearer token never removes its device-key requirement.
Generic legacy agents without an attested binding are unchanged.

The following signature protocol remains available for clients that can access
their attested key directly. The native relay transport above does not require
these per-request signatures.

`X-PowerCell-Proof` contains base64 of exact UTF-8 JSON bytes;
`X-PowerCell-Signature` contains base64 of their ECDSA P-256/SHA-256 DER signature:

```json
{"version":1,"certificate_sha256":"hex","method":"POST","target":"/agent/v1/heartbeat","timestamp":1791576000,"nonce":"uuid","body_sha256":"hex","authorization_sha256":"hex","session":"uuid","idempotency_key":null}
```

`target` is the original percent-encoded path plus query. Hashes cover raw body
bytes (empty if absent) and the exact Authorization header. Session and idempotency
fields must match their headers, or be null when absent. Reject unknown/duplicate
JSON fields, wrong context, invalid signatures, expired/revoked certificates,
stale timestamps and replayed per-certificate nonces. Nonce consumption is atomic
across API processes and survives restarts. Sign every transport retry afresh;
retain the original mutation body and idempotency key.

Proofs also operate over explicitly configured HTTP API endpoints. HTTP does not
encrypt credentials/data or authenticate responses; the ACME profile endpoint
still requires HTTPS. Optional TLS client-certificate verification may be applied
by the deployment, but forwarded certificate headers never bypass request proofs.
Only the API's own relay listener can supply the authenticated TLS identity.

## Capacity and isolation

POST `/agent/v1/capacity` requires a live session and Idempotency-Key:

```json
{"revision":7,"accepting_work":true,"mode":"idle","reason":null,"cpu_millis":4000,"memory_bytes":4294967296,"disk_bytes":8589934592,"default_isolation":"vm","network_enabled":false,"internet_only":true,"running_commands":2,"cpu_used_millis":320,"memory_used_bytes":150994944}
```

Returns `{"revision":7,"accepted":true}`. Revisions are monotonic per session;
identical retries are safe, conflicts/stale revisions return 409. Reports do not
renew the lease. New Power Cell sessions accept no work until their first report.

The offered target is separate from inventory and reservation accounting. A report
may offer zero or less than occupied capacity; it cannot free reservations.
Increases expand the stored inventory envelope. Scheduling uses both inventory
and target bounds; `free=max(0,min(inventory,target)-reserved)`. Computer responses
include targets, latest telemetry and `free_cpu/free_memory/free_disk`.
`only_available` summaries and availability history use the current offered budget.
Regular inventory/history continues to describe inventory and reservations.

When `accepting_work=false`, or existing reservations exceed the new target, no
new create/run/snapshot may be delivered or started. Cancels/destroys and completion
of already running commands remain available. Snapshot API calls return 409 while
draining. Controller and agent admission both enforce the policy under the same
transaction lock.

Isolate creation accepts `isolation_mode: "vm"|"shared"|null` and
`retry_allowed: true` by default. A plugin value takes priority; null resolves to
the chosen agent's default. The effective mode and network policy are fixed at
assignment and included in the create payload. Unadvertised modes are not scheduled.
Later capacity reports affect new isolates only.

## Confirmed cleanup and deferred diagnostics

POST `/agent/v1/isolates/{id}/evict` with live session/Idempotency-Key:

```json
{"reason":"capacity_reduced","cleanup_confirmed":true,"retry_allowed":true}
```

Reasons: `capacity_reduced`, `isolate_lost`, `disabled`, `battery`, `thermal`,
`user_switched`, `sleep`, `disk_unavailable`, `agent_quit`, `logout`,
`lease_expired`, `memory_pressure`. Confirmation means the agent has already
stopped all relevant processes/VMs. The transaction fails remaining commands as
`isolate_failed`, obsoletes assignments and releases reservations exactly once.
Terminal results are immutable. Plugin retry prohibition cannot be relaxed by the
agent. No command or stateful history is automatically replayed.

POST `/agent/v1/disconnect` takes `cleanup_confirmed:true,reason` and a live session;
it settles all work and immediately marks the computer unavailable. Its exact
retry succeeds after disconnection. Credential remains usable for a clean start.

POST `/agent/v1/unbind` takes `cleanup_confirmed:true`; no live session or user
token is required. It settles work, removes ownership, revokes credential and
certificate. For 24 hours, the revoked token plus device proof can retry only that
exact unbind. It never authorizes a new operation. Physical identity is retained.

If the device key/profile is lost or its certificate has expired, the owner can
log in again and POST `/v1/computers/{computer_id}/unbind` with a user token
having `manage` scope, `Idempotency-Key` and `{"cleanup_confirmed":true}`. The
client must stop its sandboxes before confirming. Current personal ownership or
company administration is required. This performs the same cleanup and revokes
the old credential/certificate without requiring their possession; it does not
authorize execution or issue replacement credentials. Computer, execution and
physical-device history are retained. An exact retry with the same authenticated
account/key (including a fresh login token) succeeds for 24 hours after detachment. Reconnecting requires a new
attested enrollment.

PUT `/agent/v1/diagnostics/{uuid}` requires agent credential/proof, no live session.
Send `application/octet-stream` containing the Swift agent's UTF-8 JSON record:

```json
{"id":"uuid","created":"2026-10-10T12:00:00Z","message":"failure","session_id":null,"task_id":null,"uploaded":false}
```

The URL and body IDs must match. Task metadata must belong to that computer and,
if supplied, the original session. Session-only metadata is an untrusted label.
Return `{id,sha256}`. Exact bytes deduplicate; conflicting bytes return 409. Bounded
records, checksum, original owner and retention are stored atomically in the DB.
GET `/v1/computers/{id}/diagnostics/{uuid}` requires authenticated computer
administration and original log ownership. Transferring the computer does not
transfer private logs. Storage quota exhaustion returns 409; oversize returns 413.
These logs never update execution results. stdout/stderr retain the existing live
task artifact protocol and the existing throng result shape.

## Validation and references

Tests use the repository's real Uvicorn TCP server, migrated temporary database,
native HTTPX clients, generated certificate chains and real signatures. Test
fixtures substitute an isolated trust root; production has no trust-root override.
They cover malformed/replayed attestations, signature/context substitution,
physical identity deduplication, concurrent nonce/revision/completion races,
credential recovery/revocation, cleanup, diagnostics and migration compatibility.
A physical Mac/Apple attestation round trip requires the deployed HTTPS issuer and
manual installation of the profile; generated PKI tests do not claim to validate
that external Apple service or macOS keychain access.

- [Apple Managed Device Attestation](https://support.apple.com/guide/security/managed-device-attestation-sec8a37b4cb2/web)
- [Apple ACME payload specification](https://github.com/apple/device-management/blob/release/mdm/profiles/com.apple.security.acme.yaml)
- [Apple Private PKI](https://www.apple.com/certificateauthority/private/)
- [Apple native ACME client](https://github.com/apple-oss-distributions/Security/blob/main/OSX/sec/Security/SecCertificateRequest.m)
- [RFC 8555](https://www.rfc-editor.org/rfc/rfc8555)
