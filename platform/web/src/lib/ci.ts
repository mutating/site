import type { MessageKey } from "./i18n";
import type { CIRun, RepositoryState } from "./github";
export const connectionLabel: Record<RepositoryState, MessageKey> = { unconfigured: "repositoryUnconfigured", pending: "repositoryPending", connected: "repositoryConnected", disabled: "repositoryDisabled" };
export function runLabel(run: Pick<CIRun, "status" | "reason">): MessageKey {
  const reasons: Record<string, MessageKey> = { github_success: "ciSucceeded", github_failure: "ciFailed", github_cancelled: "ciCancelled", github_timed_out: "ciTimedOut", github_skipped: "ciSkipped", github_neutral: "ciNeutral", github_action_required: "ciActionRequired", github_stale: "ciStale", github_startup_failure: "ciFailed" };
  const statuses: Record<string, MessageKey> = { preparing: "ciPreparing", ready: "ciSelected", selected: "ciSelected", running: "ciRunning", completed: "ciCompleted", cancelled: "ciCancelled", timed_out: "ciTimedOut", failed: "ciFailed" };
  return reasons[run.reason ?? ""] ?? statuses[run.status] ?? "ciDetailsInGitHub";
}
export function runReason(reason: string | null): MessageKey | null {
  const labels: Record<string, MessageKey> = { no_capacity: "ciNoCapacity", concurrency_limit: "ciConcurrencyLimit", preparation_timeout: "ciPreparationTimeout", bootstrap_cancelled: "ciPreparationCancelled", route_disabled: "ciRouteDisabled", authorization_revoked: "ciAuthorizationChanged", github_unavailable: "ciGitHubUnavailable", runner_unavailable: "ciRunnerUnavailable", isolate_unavailable: "ciIsolateUnavailable", isolate_quota: "ciIsolateQuota", runner_timeout: "ciTimedOut" };
  return labels[reason ?? ""] ?? null;
}
export function catalogReturn(value: string | null) { return value === "/actions" || value?.startsWith("/actions?") ? value : "/actions"; }
