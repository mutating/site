import { useEffect, useLayoutEffect, useRef, useSyncExternalStore, type FocusEvent } from "react";
import { allPages, api, ApiError, json, request, type ApiResponse } from "./api";
import { tabNotifications } from "./tabNotifications";
import type { Company } from "./types";
import { useData, useRefreshWhileVisible } from "./useData";
import { useReloadOnOrganizationChange } from "./useOrganizationDirectory";

export type ConnectedPoolOrigin = { entry: string; userId: string; computerId: string };

export class OutdatedPoolRead extends Error {
  constructor(public checkedAt: number) {
    super("A newer pool check superseded this read");
  }
}

export function usePoolRead<T>(read: (signal: AbortSignal) => Promise<T>, key: string, retainData: boolean, {
  revision, retainError, onAccessError,
}: { revision?: number; retainError: boolean; onAccessError?: (error: ApiError) => boolean }) {
  const retried = useRef<{ key: string; revision: number } | null>(null);
  const state = useData(read, key, retainData, {
    refreshKey: revision, retainError,
    acceptError: (error) => {
      const outdated = error instanceof OutdatedPoolRead;
      if (!outdated && (!(error instanceof ApiError) || (error.status !== 403 && error.status !== 404) ||
          onAccessError?.(error) !== false)) return true;

      // A newer check settled access while this read was in flight.
      const checkedAt = outdated ? error.checkedAt : error.poolCheckRevision;
      if (checkedAt !== undefined && (retried.current?.key !== key || retried.current.revision !== checkedAt)) {
        retried.current = { key, revision: checkedAt };
        state.reload();
      }
      return false;
    },
  });
  return state;
}

type VerifiedPoolAssignment = {
  companyId: string | null;
  membershipId?: string;
  revision: number;
  canRead: boolean;
  assigned?: boolean;
};

type ComputerPoolChange = {
  userId: string;
  computerId: string;
  previousCompanyId: string | null;
  companyId?: string | null;
  previousRevision?: number;
  settledKey?: string;
  assignment?: VerifiedPoolAssignment;
  superseded?: boolean;
  checkedRevision?: number;
};
const computerPoolListeners = new Set<(change: ComputerPoolChange) => void>();
const poolCheckRevisions = new Map<string, number>();

function publishComputerPoolChange(change: ComputerPoolChange) {
  const revision = change.assignment?.revision ?? change.checkedRevision;
  if (revision !== undefined) {
    const key = poolMoveKey(change.userId, change.computerId);
    const previous = poolCheckRevisions.get(key);
    if (previous !== undefined && revision <= previous) {
      change = { ...change, assignment: undefined, superseded: true };
    } else if (change.assignment) {
      poolCheckRevisions.set(key, revision);
    }
  }
  for (const listener of computerPoolListeners) listener(change);
}

function receiveComputerPoolChange(change: Partial<ComputerPoolChange> | null) {
  if (typeof change?.userId !== "string" || typeof change.computerId !== "string" ||
      !(change.previousCompanyId === null || typeof change.previousCompanyId === "string")) return;
  const key = poolMoveKey(change.userId, change.computerId);
  if (typeof change.settledKey === "string" && poolMoves.get(key)?.idempotencyKey === change.settledKey) {
    updatePoolMoves(key);
  }
  const assignment = change.assignment;
  publishComputerPoolChange({
    userId: change.userId, computerId: change.computerId, previousCompanyId: change.previousCompanyId,
    companyId: change.companyId === null || typeof change.companyId === "string" ? change.companyId : undefined,
    previousRevision: typeof change.previousRevision === "number" ? change.previousRevision : undefined,
    checkedRevision: Number.isSafeInteger(change.checkedRevision) ? change.checkedRevision : undefined,
    assignment: assignment && Number.isSafeInteger(assignment.revision) && typeof assignment.canRead === "boolean" &&
      (assignment.companyId === null || typeof assignment.companyId === "string" && typeof assignment.membershipId === "string")
      ? assignment : undefined,
  });
}

const publishToOtherTabs = tabNotifications("tmc-computer-pool-change", (message) =>
  receiveComputerPoolChange(message as Partial<ComputerPoolChange> | null),
);

function notifyComputerPoolChange(change: ComputerPoolChange) {
  const { userId, computerId, previousCompanyId, companyId, previousRevision, settledKey, assignment, checkedRevision } = change;
  const event = { userId, computerId, previousCompanyId, companyId, previousRevision, settledKey, assignment, checkedRevision };
  publishComputerPoolChange(event);
  publishToOtherTabs(event);
}

export function useReloadOnComputerPoolChange(userId: string, reload: (change: ComputerPoolChange) => void, companyId?: string | null) {
  useLayoutEffect(() => {
    const listener = (change: ComputerPoolChange) => {
      const assignment = change.assignment;
      const verifiedCompany = assignment && (assignment.canRead && assignment.assigned !== false || assignment.companyId !== null)
        ? assignment.companyId : undefined;
      if (change.userId === userId && (companyId === undefined || change.companyId === undefined ||
          companyId === change.previousCompanyId || companyId === change.companyId || companyId === verifiedCompany)) reload(change);
    };
    computerPoolListeners.add(listener);
    return () => { computerPoolListeners.delete(listener); };
  }, [userId, reload, companyId]);
}

export type PoolMove = ComputerPoolChange & {
  companyId: string | null;
  idempotencyKey: string;
  unsettled: boolean;
};
export class PoolMoveCheckError extends Error {
  constructor(readonly idempotencyKey: string, cause: unknown) {
    super("Could not confirm the computer pool change", { cause });
  }
}
const poolMoveStorage = "tmc-computer-pool-moves";
const poolMoveKey = (userId: string, computerId: string) => `${userId}/${computerId}`;
const poolMoveListeners = new Set<() => void>();
const resumedPoolMoves = new Set<string>();

function readPoolMoves() {
  const saved = new Map<string, PoolMove>();
  try {
    const items = JSON.parse(sessionStorage.getItem(poolMoveStorage) ?? "[]");
    const navigation = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    if (Array.isArray(items)) for (const move of items) {
      if (!move || typeof move.userId !== "string" || typeof move.computerId !== "string" ||
          typeof move.idempotencyKey !== "string" ||
          !(move.companyId === null || typeof move.companyId === "string") ||
          !(move.previousCompanyId === null || typeof move.previousCompanyId === "string")) continue;
      saved.set(poolMoveKey(move.userId, move.computerId), { ...move, unsettled: true });
      if (move.unsettled || navigator.locks || navigation?.type === "reload") resumedPoolMoves.add(move.idempotencyKey);
    }
  } catch { /* Storage can be disabled by the browser. */ }
  return saved;
}

let poolMoves = readPoolMoves();

function updatePoolMoves(key: string, move?: PoolMove) {
  poolMoves = new Map(poolMoves);
  if (move) poolMoves.set(key, move);
  else poolMoves.delete(key);
  try {
    if (poolMoves.size) sessionStorage.setItem(poolMoveStorage, JSON.stringify([...poolMoves.values()]));
    else sessionStorage.removeItem(poolMoveStorage);
  } catch { /* Keep in-memory recovery when storage is unavailable. */ }
  for (const listener of poolMoveListeners) listener();
}

function subscribeToPoolMoves(listener: () => void) {
  poolMoveListeners.add(listener);
  return () => { poolMoveListeners.delete(listener); };
}

export function useComputerPoolMoves(userId: string) {
  const moves = useSyncExternalStore(subscribeToPoolMoves, () => poolMoves);
  return (computerId: string) => moves.get(poolMoveKey(userId, computerId));
}

export function changeComputerPool(change: ComputerPoolChange, companyId: string | null) {
  const key = poolMoveKey(change.userId, change.computerId);
  const move = {
    ...change, companyId, idempotencyKey: crypto.getRandomValues(new Uint32Array(4)).join("-"), unsettled: false,
  };
  // A duplicated tab can inherit sessionStorage while this tab still owns
  // the request. Publish the intent only after acquiring the shared lock.
  return withPoolMoveLock(move, async () => {
    if (poolMoves.has(key)) throw new ApiError(409, "Computer pool change is in progress");
    updatePoolMoves(key, move);
    let receipt: ApiResponse<unknown>;
    try {
      receipt = await requestComputerPoolMove(move, false);
    } catch (error) {
      if (!(error instanceof ApiError) || [0, 408].includes(error.status) || error.status >= 500) {
        try { return await reconcilePoolMove(move); }
        catch (unsettled) {
          updatePoolMoves(key, { ...move, unsettled: true });
          notifyComputerPoolChange(move);
          throw new PoolMoveCheckError(move.idempotencyKey, unsettled);
        }
      }
      finishComputerPoolMove(move,
        [403, 404].includes(error.status) || error.detail === "Computer pool changed" ||
        error.detail === "Computer has an unfinished native transfer", { checkedRevision: error.poolCheckRevision });
      throw error;
    }
    finishComputerPoolMove(move, true, { assignment: verifiedPoolAssignment(receipt) });
    return true;
  });
}

function verifiedPoolAssignment(receipt: ApiResponse<unknown>): VerifiedPoolAssignment | undefined {
  if (receipt.poolCheckRevision === undefined || receipt.computerReadAllowed === undefined) return undefined;
  return {
    companyId: receipt.organizationId ?? null,
    membershipId: receipt.organizationMembershipId,
    revision: receipt.poolCheckRevision,
    canRead: receipt.computerReadAllowed,
    assigned: receipt.computerPoolAssigned,
  };
}

function finishComputerPoolMove(move: PoolMove, reload: boolean, confirmation: Pick<ComputerPoolChange, "assignment" | "checkedRevision"> = {}) {
  const key = poolMoveKey(move.userId, move.computerId);
  if (poolMoves.get(key)?.idempotencyKey !== move.idempotencyKey) return;
  updatePoolMoves(key);
  if (reload) notifyComputerPoolChange({ ...move, settledKey: move.idempotencyKey, ...confirmation });
}

function withPoolMoveLock<T>(move: PoolMove, run: () => Promise<T>) {
  return navigator.locks ? navigator.locks.request(`tmc-pool-move:${move.idempotencyKey}`, run) : run();
}

function requestComputerPoolMove(move: PoolMove, reconcile: boolean) {
  const path = `connected-computers/${encodeURIComponent(move.computerId)}/pool`;
  return request<unknown>(reconcile ? `${path}/reconcile` : path, {
    ...json(reconcile ? "POST" : "PATCH", {
      company_id: move.companyId,
      expected_company_id: move.previousCompanyId,
      expected_user_id: move.userId,
      expected_revision: move.previousRevision,
    }),
    headers: { "Idempotency-Key": move.idempotencyKey },
  });
}

async function reconcilePoolMove(move: PoolMove) {
  const receipt = await requestComputerPoolMove(move, true);
  if (typeof receipt.data !== "boolean") throw new Error("Invalid pool change result");
  finishComputerPoolMove(move, true, {
    assignment: receipt.data ? verifiedPoolAssignment(receipt) : undefined,
    checkedRevision: receipt.poolCheckRevision,
  });
  return receipt.data;
}

export function reconcileComputerPoolMove(move: PoolMove) {
  return withPoolMoveLock(move, async () => {
    try { return await reconcilePoolMove(move); }
    catch (error) { throw new PoolMoveCheckError(move.idempotencyKey, error); }
  });
}

export function useResumeComputerPoolMoves(userId: string) {
  useEffect(() => {
    for (const move of poolMoves.values()) {
      if (move.userId === userId && resumedPoolMoves.delete(move.idempotencyKey)) {
        void reconcileComputerPoolMove(move).catch(() => {});
      }
    }
  }, [userId]);
}

export function poolPath(companyId: string | null) {
  return `/compute/pools/${companyId ? encodeURIComponent(companyId) : "personal"}`;
}

function poolLabelKey(label: string) {
  return label.trim().replace(/\s+/gu, " ").normalize("NFC");
}

export function poolLabel(name: string | null, personal: string, organization: string) {
  if (name === null) return personal;
  return poolLabelKey(name) === poolLabelKey(personal) ? `${name} (${organization})` : name;
}

export function organizationPoolLabels(
  companies: readonly { id: string; name: string }[],
  personal: string,
  organization: string,
) {
  const rows = companies.map(({ id, name }) => {
    const label = poolLabel(name, personal, organization);
    return { id, label, key: poolLabelKey(label) };
  });
  const counts = new Map<string, number>();
  for (const { key } of rows) counts.set(key, (counts.get(key) ?? 0) + 1);
  return new Map(rows.map(({ id, label, key }) => [id, (counts.get(key) ?? 0) > 1 ? `${label} · ${id}` : label]));
}

export function revealFocusedComputeControl({ target }: FocusEvent<HTMLElement>) {
  if (!target.matches(":focus-visible")) return;
  const header = document.querySelector(".site-header");
  if (
    header && getComputedStyle(header).position === "sticky" &&
    target.getBoundingClientRect().top < header.getBoundingClientRect().bottom + 4
  ) {
    target.scrollIntoView({ block: "start", inline: "nearest" });
  }
}

export function useComputeCompanies(userId: string, beforeUpdate?: () => void) {
  const companies = useData(async (signal) => {
    const rows = await allPages<Company>("companies", signal);
    if (!signal.aborted) beforeUpdate?.();
    return rows;
  }, userId, true, { retainError: true });
  useRefreshWhileVisible(companies.reload);
  useReloadOnOrganizationChange(userId, "all", (change) => {
    if (change.type === "removed") {
      beforeUpdate?.();
      companies.update((rows) => rows?.filter((company) =>
        change.membershipId ? company.my_membership_id !== change.membershipId : company.id !== change.companyId,
      ) ?? null);
    }
    companies.reload();
  }, { includeMemberChanges: true });
  return companies;
}
