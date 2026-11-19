import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { api } from "./api";
import type { Company, CompanyAppearance, Invitation, Membership } from "./types";
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
  | { type: "invitationsChanged" }
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

export function notifyOrganizationChange(change: OrganizationChange) {
  for (const listener of [...organizationChangeListeners]) {
    listener(change);
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
