# Power Cell server protocol

Power Cell registration is a client-generated installation id plus a normal
agent credential. Migration `0028` removes device certificates, ACME
enrollments, request nonces and the native relay; migration `0029` removes the
server-side encrypted credential copy.

## Deployment

Run `throng-api migrate` before starting an updated API/controller. Power Cell
has no credential-encryption setting: the client generates the agent credential
and sends only its SHA-256 hash to `finish`. The server stores that hash in
`agent_credentials` and returns only `{computer_id, device_id}`. There is no CA,
profile, relay listener or certificate renewal hook to configure.

Additional limits: `THRONG_ENROLLMENT_SECONDS` (3600),
`THRONG_AGENT_MUTATION_SECONDS` (86400), `THRONG_MAX_DIAGNOSTIC_BYTES` (8 MiB),
`THRONG_MAX_COMPUTER_DIAGNOSTICS_BYTES` (64 MiB),
`THRONG_DIAGNOSTIC_RETENTION_DAYS` (30).

## Device login

`POST /api/v1/auth/login` accepts `device_id` (UUID). The server stores it in
`devices` and associates it with the authenticated user in `device_accounts`.
The id may be reused by another account on the same computer; it is an
installation identifier, not a hardware proof.

## Registration

`POST /api/v1/agent-enrollments` requires that the device already signed in for
the same account. The response is immediately verified; no profile challenge
exists. The client generates the `agt_` credential, sends its SHA-256 hash as
`{"credential_hash": "..."}` to `finish`, and keeps the raw credential locally.
`finish` returns `{computer_id, device_id}`; repeating the same hash and
idempotency key returns the same computer, while a different hash or key
conflicts.

`assign_pool` defaults to false. Without it the computer has no
`ComputerOwnership`: it is visible to its account, cannot receive tasks, and can
be assigned later from the app or dashboard. Set `assign_pool` and `company_id`
to bind personal (`null`) or an organization pool at registration.

## Agent authentication

Agent endpoints accept the `agt_` bearer credential plus the live
`X-Agent-Session` and idempotency headers where required. There are no request
signatures, certificates, nonces or relay connections.

## Data migration

`0028` renames `physical_devices` to `devices`, keeps existing device ids,
backfills account associations from connected computers, preserves pending
enrollment installation ids as device keys, and drops certificate/nonce tables
and certificate columns. `0029` drops `agent_enrollments.credential_encrypted`
because the client owns the raw credential and the server keeps only the hash
in `agent_credentials`. Existing computers keep their credentials and become
normal credential-authenticated workers.
