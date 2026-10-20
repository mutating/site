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
`X-Agent-Session`. It returns `{ "accepted": bool, "active": bool }`.

* `accepted` confirms that this exact normalized completion was committed earlier.
  Different content returns `completion_conflict` rather than confirming a result.
* `active` indicates that the original assignment can still accept a completion.
  It is false for expired/superseded sessions and terminal/expired tasks.
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

The `throng.transport` logger emits JSON for HTTP attempts: UUID request ID,
method, **route template**, HTTP status, monotonic processing duration, global DB
write-lock wait and number of write transactions. Raw URL parameters/query strings,
request bodies, credentials and signatures are excluded. `X-Request-ID` accepts a
UUID or is replaced by a server-generated UUID, and is returned to the caller.
Relay idle/backend failures log their close reason. Empty queue polling uses reads
and rechecks under the mutation lock before dispatching; mTLS checks use reads,
while signed proof nonce consumption remains durable and serialized.

## Continuous verification

`.github/workflows/network-recovery.yml` checks out the native client and invokes
its `scripts/network-ci.sh` on Apple Silicon macOS. It runs production Agent
recovery tests, native URLSession against a real API through a loopback TCP fault
proxy, authenticated TLS/HTTP2 relay tests, and explicit regression mutations.
`POWER_CELL_NETWORK_REQUIRED=1` makes a missing native binary an error. Normal Linux
API jobs may skip native-only scenarios; the dedicated job must not skip them.

The fault model includes dropped requests, lost committed responses, truncated
responses, interrupted uploads, delays, lease expiry, stale sessions, duplicate
notifications, conflicting results, process restart and parallel control/bulk
traffic. Tests do not access a deployed server, install Apple profiles or change a
VPN. They cannot certify every possible packet route, kernel or hardware failure.
Mutation logs, JSON results and JUnit output are uploaded even when the gate fails.
The main Tests workflow requires this reusable gate on every push, PR and deployment
verification. For coordinated branches, workflow_dispatch accepts `client_ref`.
Client-side CI runs the native recovery/mutation gate without needing access to this
private repository. Publish the compatible public client commit first. These
verification workflows never deploy changes themselves.
