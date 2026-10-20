# Agent transport recovery

The server advertises `powercell.recovery.v1`. Deploy this additive protocol before
the updated macOS client. Existing request bodies and `detail` error messages stay
compatible; selected protocol rejections additionally expose a stable `code`:
`session_expired`, `task_expired`, `task_terminal`, `completion_conflict`,
`idempotency_conflict`, `capacity_conflict`, `agent_not_accepting`,
`task_not_awaiting_ack`, `task_not_startable`, and `isolate_session_expired`.
Do not interpret every HTTP 409 as an expired session or a successful delivery.

`POST /agent/v1/tasks/{task_id}/receipt` accepts the same TaskComplete JSON as
`complete`, the current device credential/proof and the **original**
`X-Agent-Session`. It returns
`{ "accepted": bool, "active": bool, "task_status": string }`.

* `accepted` confirms that this exact normalized completion was committed earlier.
  Different content returns `completion_conflict` rather than confirming a result.
* `active` indicates that the original assignment can still accept a completion.
  It is false for expired/superseded sessions and terminal/expired tasks.
* `task_status` reports the original assignment state. A terminal cancel can be
  settled locally without treating its unaccepted result as confirmation of a
  linked run's delivery.
* Receipt lookup does not renew leases, dispatch work, change completion state or
  revive execution rights. Unknown/foreign tasks or a different original session
  are not disclosed. Current credential and device checks always apply.

`ack` and `started` are already repeatable while their assignment is active.
`complete` deduplicates normalized content; artifact PUTs deduplicate size/SHA-256.
Session/capacity/eviction/cleanup operations retain their existing idempotency keys.
Retries must use a new proof nonce/signature, never reuse an accepted signed proof.

The inner relay HTTP keepalive is explicitly configured by
`THRONG_POWERCELL_RELAY_KEEP_ALIVE_SECONDS` (default 30, range 5–300). This is an
idle timeout, independent of request execution budgets and long polling. Keeping
a connection open longer reduces races during measured 5–10 second delivery
interruptions; clients must still recover from arbitrary connection closure.

An abnormal backend TCP close resets only its HTTP/2 CONNECT stream with
`CONNECT_ERROR`, as required by RFC 9113 section 8.5. A normal backend EOF sends
`END_STREAM`. Failed error signaling after a peer reset or outer transport close
still releases the backend; unrelated streams retain the connection.

The `throng.transport` logger emits JSON for HTTP attempts: UUID request ID,
method, **route template**, HTTP status, monotonic processing duration, global DB
write-lock wait and number of write transactions. Raw URL parameters/query strings,
request bodies, credentials and signatures are excluded. `X-Request-ID` accepts a
UUID or is replaced by a server-generated UUID, and is returned to the caller.
Relay idle/backend failures log their close reason. Empty queue polling uses reads
and rechecks under the mutation lock before dispatching; mTLS checks use reads,
while signed proof nonce consumption remains durable and serialized.
Lock wait includes failed and cancelled acquisitions; the write count includes
only successfully acquired transactions. An interrupted request therefore keeps
the timing needed to distinguish database contention from transport delay.

CI reservation requires both local creator authorization and a successful GitHub
repository check. Restoring local company permissions between the read snapshot
and final write lock cannot turn a skipped GitHub check into approval. A rejected
reservation records its authorization failure reason and allocates no isolate.

## Continuous verification

The server's ordinary API jobs verify receipts, exact replay, session expiry,
authorization, transport logging and authenticated TLS/HTTP2 relay behavior using
simulated clients and local sockets. Fault-proxy lifecycle tests remain here as
server test-fixture checks. These jobs do not check out or build the native
application, and server releases do not depend on an application source revision.

Native Swift/URLSession recovery, Agent/outbox behavior and client mutation tests
belong to the macOS application's CI. Its `network-api-contracts.yml` runs the real
client against a self-contained local mock API, including dropped requests, lost
committed replies, truncated responses, interrupted uploads, delays and exact
completion receipts. It does not import or start this server repository.

The two repositories verify their own side of the documented protocol independently.
Client mocks describe the expected wire contract; server tests validate the real
implementation. Neither CI requires publishing the other repository first. Tests
do not access a deployed server, install Apple profiles or change a VPN, and do
not claim to cover every possible kernel, hardware or network failure.
