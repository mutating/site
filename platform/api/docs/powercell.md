# Power Cell server protocol

Power Cell registration is a client-generated installation id plus a normal
agent credential. Migration `0028` removes device certificates, ACME
enrollments, request nonces and the native relay.

## Deployment

Run `throng-api migrate` before starting an updated API/controller. The only
Power Cell-specific setting is the persistent secret used to recover a
credential after an uncertain finish response:

```dotenv
THRONG_ENROLLMENT_SECRET=<at least 32 random characters>
```

Generate it once with `openssl rand -hex 32`, back it up with the database, and
keep it stable across restarts. Changing it makes unfinished idempotent finish
retries unavailable. Agent credentials remain hashed in `agent_credentials`;
the secret never becomes a trust anchor.

Without the secret the API serves generic workers and scheduling, but
`POST /agent-enrollments/{id}/finish` returns `503`. Existing credentials keep
working. There is no CA, profile, relay listener or certificate renewal hook to
configure.

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
exists. `finish` returns `{computer_id, credential, device_id}`. The
idempotency key recovers the same credential; a different key conflicts.

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
and certificate columns. Existing computers keep their credentials and become
normal credential-authenticated workers.
