import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { tabNotifications } from "./tabNotifications";
import type { Company, CompanyAppearance, Invitation, InvitationLinkCheck, Membership } from "./types";
import {
  ORGANIZATIONS_PAGE_SIZE,
  organizationEntries,
  type OrganizationEntry,
} from "./organizations";

type Item =
  | { kind: "company"; group: 1 | 2; company: Company }
  | { kind: "invitation"; group: 0; invitation: Invitation };
type Page = { entries: OrganizationEntry[]; total: number; totalAll: number };
const pageKey = (user: string, query: string, page: number) =>
  JSON.stringify([user, query, page]);

type OrganizationScopeChange = {
  userId: string;
  companyId: string;
  source: symbol;
} & (
  | { type: "removed"; membershipId?: string }
  | { type: "renamed"; name: string }
  | { type: "appearanceUpdated"; appearance: CompanyAppearance }
  | { type: "membershipUpdated"; member: Membership }
  | { type: "membersChanged" }
  | { type: "invitationsChanged"; invitation?: InvitationLinkCheck }
);
type OrganizationChange = { userId: string; type: "created" } | OrganizationScopeChange;
type OrganizationChangeListener = (change: OrganizationChange) => void;
const organizationChangeListeners = new Set<OrganizationChangeListener>();

export function subscribeToOrganizationChanges(listener: OrganizationChangeListener) {
  organizationChangeListeners.add(listener);
  return () => {
    organizationChangeListeners.delete(listener);
  };
}

function publishOrganizationChange(change: OrganizationChange) {
  for (const listener of [...organizationChangeListeners]) {
    listener(change);
  }
}

type SharedOrganizationChange = {
  userId: string;
  companyId?: string;
  type: "created" | "removed" | "renamed" | "membersChanged" | "invitationsChanged";
  membershipId?: string;
  name?: string;
  invitation?: InvitationLinkCheck;
};
const shareOrganizationChange = tabNotifications("tmc-organization-change", (message) => {
  const change = message as Partial<SharedOrganizationChange> | null;
  if (typeof change?.userId !== "string") return;
  if (change.type === "created") {
    publishOrganizationChange({ type: "created", userId: change.userId });
    return;
  }
  if (typeof change.companyId !== "string") return;
  const scope = { userId: change.userId, companyId: change.companyId, source: Symbol() };
  if (change.type === "removed") {
    const membershipId = typeof change.membershipId === "string" ? change.membershipId : undefined;
    publishOrganizationChange({ ...scope, type: "removed", membershipId });
  } else if (change.type === "renamed" && typeof change.name === "string") {
    publishOrganizationChange({ ...scope, type: "renamed", name: change.name });
  } else if (change.type === "membersChanged") {
    publishOrganizationChange({ ...scope, type: "membersChanged" });
  } else if (change.type === "invitationsChanged") {
    const check = change.invitation;
    const invitation = typeof check?.id === "string" &&
      (check.status === "revoked" || check.status === "pending" &&
        typeof check.link_version === "string" && Number.isSafeInteger(check.link_check_revision)) ? check : undefined;
    publishOrganizationChange({ ...scope, type: "invitationsChanged", invitation });
  }
});

export function notifyOrganizationChange(change: OrganizationChange) {
  publishOrganizationChange(change);
  if (change.type === "created") {
    shareOrganizationChange(change);
  } else if (change.type === "removed" || change.type === "renamed" || change.type === "membersChanged" || change.type === "invitationsChanged") {
    const { source, ...message } = change;
    shareOrganizationChange(message);
  }
}

export function useReloadOnOrganizationChange(
  userId: string,
  scope: string | null,
  reload: (change: OrganizationChange) => void,
  { includeMemberChanges = false }: { includeMemberChanges?: boolean } = {},
) {
  useLayoutEffect(() => {
    if (!scope) return;
    return subscribeToOrganizationChanges((change) => {
      if (change.type !== "invitationsChanged" &&
          (includeMemberChanges || change.type !== "membersChanged") && change.userId === userId &&
          (scope === "all" || (change.type !== "created" && change.companyId === scope))) {
        reload(change);
      }
    });
  }, [userId, scope, reload, includeMemberChanges]);
}

export type OrganizationMemberPermission = "members.read" | "members.manage";

export function useOrganizationSectionAccess(
  { error, reload }: { error: unknown; reload: () => void },
  denial: ApiError | null,
  grantedAt?: number,
) {
  const precedesGrant = (failure: unknown) => failure instanceof ApiError &&
    (failure.status === 403 || failure.status === 404) && failure.poolCheckRevision !== undefined &&
    grantedAt !== undefined && grantedAt > failure.poolCheckRevision;
  const activeDenial = precedesGrant(denial) ? null : denial;
  const outdatedError = !activeDenial && precedesGrant(error);
  const restored = !activeDenial && (denial !== null || outdatedError);
  useEffect(() => {
    if (restored) reload();
  }, [restored, reload]);
  return { denial: activeDenial, error: outdatedError ? null : error };
}

export function useOrganizationMembershipCheck(key: string) {
  type CheckedAt = { key: string; id: string; revision?: number };
  const checked = useRef<CheckedAt | null>(null);
  const permissionCheckedAt = useRef<CheckedAt | null>(null);
  const memberPermissions = useRef<Partial<Record<OrganizationMemberPermission, CheckedAt & { allowed: boolean }>>>({});
  const [permission, setPermission] = useState<{ key: string; id: string; resourcesRead: boolean } | null>(null);
  const remember = useCallback(({ id, revision, resourcesRead }: { id: string; revision?: number; resourcesRead?: boolean }) => {
    const latest = checked.current;
    const newerIdentity = latest?.key === key && latest.revision !== undefined &&
      (revision === undefined || revision < latest.revision);
    if (newerIdentity && latest.id !== id) return false;
    const incoming = { key, id, revision };
    if (!newerIdentity) checked.current = incoming;

    // A roster read can confirm the same membership without checking its resource permission.
    const lastPermission = permissionCheckedAt.current;
    const samePermissionScope = lastPermission?.key === key && lastPermission.id === id;
    if (resourcesRead !== undefined && samePermissionScope && lastPermission.revision !== undefined &&
        (revision === undefined || revision < lastPermission.revision)) return false;
    if (resourcesRead !== undefined) permissionCheckedAt.current = incoming;
    else if (!samePermissionScope) permissionCheckedAt.current = null;

    setPermission((current) => {
      const sameMembership = current?.key === key && current.id === id;
      if (resourcesRead === undefined) return sameMembership ? current : null;
      if (sameMembership && current.resourcesRead === resourcesRead) return current;
      return { key, id, resourcesRead };
    });
    return true;
  }, [key]);
  const clearPermission = useCallback(() => {
    permissionCheckedAt.current = checked.current;
    setPermission(null);
  }, []);
  const precedesMembershipCheck = useCallback((revision?: number, membershipId?: string) => {
    const latest = checked.current;
    return revision !== undefined && latest?.key === key && latest.revision !== undefined &&
      (membershipId === undefined || latest.id !== membershipId) && revision < latest.revision;
  }, [key]);
  const precedesResourcePermissionCheck = useCallback((id: string, revision?: number) => {
    const latest = permissionCheckedAt.current;
    return revision !== undefined && latest?.key === key && latest.id === id &&
      latest.revision !== undefined && revision < latest.revision;
  }, [key]);
  const newerMemberPermission = useCallback((permission: OrganizationMemberPermission, id: string, revision?: number) => {
    const latest = memberPermissions.current[permission];
    return revision !== undefined && latest?.key === key && latest.id === id &&
      latest.revision !== undefined && revision < latest.revision ? latest.allowed : undefined;
  }, [key]);
  const rememberMemberPermission = useCallback((permission: OrganizationMemberPermission, id: string, allowed: boolean, revision?: number) => {
    if (revision === undefined) return true;
    if (newerMemberPermission(permission, id, revision) !== undefined) return false;
    memberPermissions.current[permission] = { key, id, revision, allowed };
    return true;
  }, [key, newerMemberPermission]);
  return { checked, remember, clearPermission, precedesMembershipCheck, precedesResourcePermissionCheck, rememberMemberPermission,
    newerMemberPermission,
    resourcesRead: permission?.key === key ? permission.resourcesRead : undefined };
}

export function useOrganizationDirectory(
  userId: string,
  input: string,
  page: number,
) {
  const query = input.trim();
  const key = pageKey(userId, query, page);
  const [result, setResult] = useState<{ key: string; data: Page } | null>(
    null,
  );
  const [failure, setFailure] = useState<{
    key: string;
    error: unknown;
  } | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const previousQuery = useRef(query);
  const active = useRef<AbortController | null>(null);
  const firstPage = useRef<{ userId: string; data: Page } | null>(null);
  const data = result?.key === key ? result.data : null;
  const error = failure?.key === key ? failure.error : null;
  const reload = useCallback(() => {
    active.current?.abort();
    setLoading(true);
    setRevision((value) => value + 1);
  }, []);
  const remove = useCallback(
    (companyId: string, membershipId?: string) => {
      active.current?.abort();
      const without = (previous: Page): Page => {
        const entries = previous.entries.filter((entry) => {
          if (membershipId) {
            return entry.kind !== "company" || entry.company.my_membership_id !== membershipId;
          }
          const id = entry.kind === "company" ? entry.company.id : entry.invitation.company_id;
          return id !== companyId;
        });
        const difference = previous.entries.length - entries.length;
        return {
          entries,
          total: previous.total - difference,
          totalAll: previous.totalAll - difference,
        };
      };
      setResult((previous) =>
        previous?.key === key ? { key, data: without(previous.data) } : previous,
      );
      if (firstPage.current?.userId === userId)
        firstPage.current = { userId, data: without(firstPage.current.data) };
    },
    [key, userId],
  );

  useEffect(
    () => subscribeToOrganizationChanges((change) => {
      if (change.userId !== userId) return;
      if (change.type === "removed") remove(change.companyId, change.membershipId);
      reload();
    }),
    [userId, reload, remove],
  );
  useEffect(() => {
    const controller = new AbortController();
    active.current = controller;
    const delay = query && previousQuery.current !== query ? 250 : 0;
    previousQuery.current = query;
    setLoading(true);
    setFailure(null);
    async function load() {
      try {
        const params = new URLSearchParams({
          q: query,
          limit: String(ORGANIZATIONS_PAGE_SIZE),
          offset: String((page - 1) * ORGANIZATIONS_PAGE_SIZE),
        });
        const response = await api<{
          items: Item[];
          total: number;
          total_all: number;
        }>(`organization-directory?${params}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        const next: Page = {
          entries: response.items.map((item) =>
            item.kind === "company"
              ? {
                  ...item,
                  name: item.company.name,
                  id: item.company.id,
                  computerCount: item.company.computer_count,
                }
              : {
                  ...item,
                  name: item.invitation.company_name,
                  id: item.invitation.id,
                  computerCount: item.invitation.computer_count,
                },
          ),
          total: response.total,
          totalAll: response.total_all,
        };
        setResult({ key, data: next });
        if (!query && page === 1) firstPage.current = { userId, data: next };
      } catch (error) {
        if (!controller.signal.aborted) setFailure({ key, error });
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    const timer = delay ? window.setTimeout(load, delay) : null;
    if (!delay) void load();
    return () => {
      controller.abort();
      if (timer !== null) window.clearTimeout(timer);
    };
  }, [userId, query, page, key, revision]);

  function insert(company: Company) {
    active.current?.abort();
    const previous =
      firstPage.current?.userId === userId ? firstPage.current.data : null;
    const entries = previous?.entries ?? [];
    const alreadyListed = entries.some(
      (entry) => entry.kind === "company" && entry.id === company.id,
    );
    const next: Page = {
      entries: organizationEntries(
        [
          ...entries.flatMap((entry) =>
            entry.kind === "company" && entry.id !== company.id
              ? [entry.company]
              : [],
          ),
          company,
        ],
        entries.flatMap((entry) =>
          entry.kind === "invitation" ? [entry.invitation] : [],
        ),
        userId,
      ).slice(0, ORGANIZATIONS_PAGE_SIZE),
      total: (previous?.total ?? data?.totalAll ?? 0) + (alreadyListed ? 0 : 1),
      totalAll:
        (previous?.totalAll ?? data?.totalAll ?? 0) + (alreadyListed ? 0 : 1),
    };
    firstPage.current = { userId, data: next };
    setResult({ key: pageKey(userId, "", 1), data: next });
    setFailure(null);
  }
  return {
    data,
    error,
    loading: loading || (!data && !error),
    insert,
    remove,
    reload,
  };
}
