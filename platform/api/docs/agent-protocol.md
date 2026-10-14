# Agent protocol v1

The server is the durable source of assignments and results. The agent opens
outbound HTTPS connections; it does not expose an inbound port. This document
specifies the future agent's obligations, including behavior that server code
alone cannot enforce on an offline computer.

## Enrollment and sessions

1. An owner registers the computer using `POST /api/v1/computers`. CPU, memory and
   disk describe capacity the owner permits the service to consume, not necessarily
   the whole machine. OS, architecture and runtime are exact-match capabilities.
2. An owner calls `POST /api/v1/computers/{id}/credentials`. The returned random
   token is shown once. Store it in the agent's private configuration, never in
   an isolate. Rotating it revokes the old credential, invalidates its session and
   fails outstanding work while retaining reservations until cleanup.
3. Before establishing a NEW session, obtain the current generation from the
   journal or credential-authenticated `GET /agent/v1/session`. Stop and remove
   ALL agent-managed sandboxes and process trees from every previous generation,
   including after credential rotation, journal loss or a `null` server session.
   The observed generation is a concurrency guard, not a filter for cleanup.
   Persist a fresh
   handshake key and call `POST /agent/v1/session` with the credential,
   `Idempotency-Key: <persisted key>` and
   `{"protocol_version":1,"clean_start":true,"previous_session_id":"<observed session>"}`.
   Use `null` when no previous session exists. `clean_start` asserts completed
   cleanup; `previous_session_id` fences requests based on an obsolete generation.
   Retry an uncertain response with the SAME key and body. A current-session
   retry returns that session without cleanup or lease extension. A superseded
   key, expired session or mismatched previous generation returns `409`. After
   reconciling the current generation and completing full cleanup, use a new key. Never reuse
   an old key for a new cleanup operation.
4. Persist the returned `session_id`. Every other agent request supplies it in
   `X-Agent-Session`. A new session invalidates previous assignments and releases
   capacity only because the agent has asserted cleanup.
5. Renew the session with `POST /agent/v1/heartbeat`, well before the lease expires
   (for example every 15 seconds with the default 60-second lease). Claim requests
   do not renew it. Heartbeat runs independently of task execution and uploads.

Session responses include `server_time` and `lease_expires_at`. Calculate a
conservative local deadline from their difference and the local monotonic time
at request SEND, not receipt. Reserve a margin for delays. Handle machine sleep:
after wakeup, validate the lease before allowing work to continue. If renewal
fails until the local deadline, terminate sandbox process trees and cease work.
Never extend a lease just because the TCP connection remains open.

A transient connection failure within the lease permits reconnection with the
same session and durable agent journal. `409` for an obsolete/expired session
requires cleanup and a new session. `401` requires a valid credential. Neither
response authorizes repeating a user command in a new sandbox.

## Delivery, journal, acknowledgements

Call `POST /agent/v1/tasks/claim` with `{"wait_seconds":25,"limit":20}`. The
response includes session information and a `tasks` array. An empty response is
normal. Immediately issue the next poll; use bounded exponential backoff with
jitter after transport errors. Continue polling while commands execute so that
cancel/destroy tasks can be received promptly.

Each task includes `id`, `kind`, `computer_id`, `session_id`, `isolate_id`, optional
`command_id`, `payload` and `deadline`. Delivery is at least once. Unacknowledged
tasks can be returned repeatedly with the SAME task ID. Delivery does not mean
the command is running.

Persist the assignment in a local journal before acknowledging it through
`POST /agent/v1/tasks/{id}/ack`. Duplicates consult that journal rather than spawn
another process. Acknowledgement stops redelivery but does not complete the task
or extend its deadline. Acknowledged unfinished tasks remain inspectable through
`GET /agent/v1/tasks/{id}` after reconnection. Retain their IDs in the journal.

The journal also needs durable cancel tombstones by `command_id` and destruction
tombstones by `isolate_id`. Cancel/destroy can arrive before an earlier run/create
message is handled. Never start work covered by a tombstone, even if messages are
processed concurrently. Destruction stops all process trees and prevents delayed
creation of that isolate within the session. The server prioritizes destroy and
cancel tasks, but correctness must not depend on arrival order.

The journal cannot remove the ambiguity of a crash between recording a launch
and spawning the process. When execution cannot be reconciled safely, stop/clean
the previous session and report/recover the infrastructure failure. Do not
automatically launch an arbitrary command again.

## Task types

| Kind | Payload and obligation |
|---|---|
| `create` | `state_artifact_id`, `image`, OS/architecture/runtime, CPU/memory/disk limits. Create the sandbox, enforce limits, restore state, then acknowledge completion. |
| `run` | `command`, `shell`, `cwd`, `env`, `timeout_seconds`. Execute once inside the assigned sandbox, capture output and stop the entire process tree at its timeout. |
| `cancel` | `reason`, `run_task_id`; the envelope contains `command_id`. Persist cancellation, prevent any delayed launch, stop the process tree, then confirm completion. |
| `destroy` | Remove the isolate, its process trees and state. Confirm only when capacity can safely be reused. Already absent sandboxes count as successfully destroyed. |
| `snapshot` | Export the current sandbox state, upload the snapshot, then confirm completion. The server queues no competing commands during the snapshot. |

Commands use a string to match `throng`. With `shell=false`, use argument parsing
compatible with the plugin's documented command syntax and execute directly;
never silently enable shell interpolation. The first runtime should use the
same `suby` command semantics as the existing throng plugins. With `shell=true`,
execute through the explicitly supported sandbox shell. The agent defines and
advertises its runtime; unsupported commands/runtimes produce a failed operation.

`cwd` is relative to the isolate root. Validate it again after resolving symlinks.
For state archives, prohibit traversal, escaping links and archive expansion
beyond the disk quota. The HTTP service stores opaque bytes and does not validate
archive members. Never expose host mounts, daemon credentials or the container
engine socket to command processes. The runtime must enforce process, filesystem,
network and resource policies on the worker; the API is not a sandbox itself.

After an actual command launch, send `POST /agent/v1/tasks/{id}/started`. Repeating
this notification does not reset its start time. It changes the command to
`running` unless cancellation is already pending. A fast completion may arrive
without a separate started event; the server accepts the final result.

## Completion and output

Send `POST /agent/v1/tasks/{id}/complete`. A normal command result is:

```json
{"outcome":"completed","returncode":1,"stdout":"1 failed","stderr":""}
```

Nonzero exit codes are completed commands with `success=false`. A command that
could not be launched uses `{"outcome":"failed","error":"..."}`. When a pending
cancellation has stopped the command, its run task can report
`{"outcome":"cancelled","returncode":-15,"stdout":"partial output"}`.
For an agent-enforced local timeout, send `outcome=timed_out` with the known exit
code and captured output. It is accepted without a prior server cancellation
request, so a slow controller tick cannot lose the result of a stopped process.
Only `run` tasks accept `cancelled` or `timed_out`; control tasks accept only
`completed` or `failed`.

Alternatively, complete the cancel task with `outcome=completed` once the stop is
confirmed. If the stop cannot be confirmed, fail the cancel operation.

Successful create/destroy tasks report `{"outcome":"completed"}`. Failed create
tasks still require cleanup; the server sends a separate destroy operation.
A failed or unconfirmed destroy quarantines the computer until a clean start.

Persist the exact completion and retry it until accepted. Duplicate identical
completions are idempotent. Changed completions, obsolete tasks and messages from
old sessions return `409`; they cannot overwrite a terminal result. If normal
completion wins a race against cancellation, cancellation may subsequently be
obsolete. This is expected. A successful HTTP response is sent after the result
and command status commit in the same transaction.

Inline stdout/stderr allow up to 1,048,576 characters each and must contain valid
Unicode without NUL. Invalid text receives `422`. Binary or larger output uses
`PUT /agent/v1/tasks/{id}/artifacts/stdout` or `/stderr` with a binary body BEFORE
completion. Repeating an identical upload for a task/purpose returns the same
artifact; different bytes produce `409`. Artifacts are automatically attached to
the completion result. For cancellation, upload partial logs through the task
whose completion is being reported.
Uploads must finish before the task deadline. The server checks it both before
accepting the body and after the transfer; an expired task receives `409`.

For snapshots, upload binary bytes using
`PUT /agent/v1/tasks/{id}/artifacts/snapshot`, then complete the task. The client
polls `/api/v1/operations/{id}` and obtains `artifact_id` for downloading the
state. The plugin is responsible for the state format; it can use tar archives.

Download initial state through `GET /agent/v1/artifacts/{id}/content`. This is
authorized only for states assigned to this computer's active session. The
`X-Content-SHA256` header lets the agent verify the transfer. All agent-uploaded
artifacts inherit the isolate owner; arbitrary owner IDs are never accepted.

## Client cancellation monitor

The future throng plugin has two independent monitors:

- A result monitor batches `/api/v1/commands/statuses`, fetches terminal results
  through `/commands/results`, stores the result/error and signals the waiting
  `run()` event.
- A cancellation monitor keeps command/token pairs, periodically observes each
  live `cantok` token, and batches canceled IDs through `/commands/cancel`.

Retry cancellation requests after transport errors; the operation is idempotent.
Keep result polling active after `cancel_requested`. Remove command/token entries
only after terminal processing. Both monitors must shut down cleanly and unblock
waiters on unrecoverable errors. Isolate lifetime renewal is independent of
whether a command is currently running.

## Power Cell extensions

Power Cell uses the same task/result transport with additional attested enrollment,
capacity policies and confirmed cleanup. See [powercell.md](powercell.md) for the
wire contract, deployment settings and trust boundary. Existing generic workers
retain their original credential protocol; the `powercell-ubuntu-arm64-v1` runtime
can only be registered through verified enrollment.
