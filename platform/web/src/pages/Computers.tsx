import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { request, allPages, ApiError, withDeadline } from "../lib/api";
import { computerAvailability } from "../lib/computerAvailability";
import { poolPath, useReloadOnComputerPoolChange } from "../lib/compute";
import { useData, useRefreshWhileVisible } from "../lib/useData";
import { useReloadOnOrganizationChange } from "../lib/useOrganizationDirectory";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import { GiB, number } from "../lib/chart";
import type { Computer, Company } from "../lib/types";
import { Empty, ErrorBox, Heading, Loading } from "../components/UI";
import { BenchmarkSummary } from "../components/Benchmarks";
import { runtimeLabel } from "../lib/runtime";
export function Computers() {
  const viewer = useSession().user!.id;
  const { t, locale } = useI18n();
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const connectedPoolOrigin = location.state?.connectedPoolOrigin;
  const navigationState = connectedPoolOrigin ? { connectedPoolOrigin } : undefined;
  const navigate = useNavigate();
  const companyId = params.get("company_id")?.toLowerCase() ?? null;
  const [lostCompanyId, setLostCompanyId] = useState<string | null>(null);
  const verifiedMembership = useRef<{ companyId: string; membershipId: string; revision?: number } | null>(null);
  const showAll = params.get("only_available") === "false";
  const directoryParam = params.get("directory_search");
  const directorySearch = directoryParam?.startsWith("?") ? directoryParam : "";
  const organizationPage = params.get("organization_page");
  const memberQuery = params.get("organization_q");
  const organizationQuery = new URLSearchParams(organizationPage ? { page: organizationPage } : {});
  if (memberQuery) organizationQuery.set("q", memberQuery);
  const organizationPath = `/organizations/${encodeURIComponent(companyId ?? "")}${organizationQuery.size ? `?${organizationQuery}` : ""}`;
  const userId = params.get("user_id")?.toLowerCase() || viewer;
  const page = Math.max(
    1,
    Math.min(1_000_000, Number.parseInt(params.get("page") || "1", 10) || 1),
  );
  const company = useData(
    (signal) => allPages<Company>("companies", signal),
    viewer,
  );
  const query = new URLSearchParams({
    ...(companyId ? { company_id: companyId } : { user_id: userId }),
    only_available: String(!showAll),
    limit: "10",
    offset: String((page - 1) * 10),
  });
  const queryKey = query.toString();
  const title = useRef<HTMLHeadingElement>(null);
  const table = useRef<HTMLTableElement>(null);
  const pagination = useRef<HTMLElement>(null);
  const retry = useRef<HTMLButtonElement>(null);
  const savedFocus = useRef<{ key: string; element: Element } | null>(null);
  const rememberFocus = () => {
    const element = document.activeElement;
    if (element && (table.current?.contains(element) || pagination.current?.contains(element) || retry.current === element)) {
      savedFocus.current = { key: queryKey, element };
    }
  };
  const state = useData<{ data: Computer[]; total: number | null; queryKey: string; poolCheckRevision?: number }>(
    async (signal) => {
      const read = (listSignal: AbortSignal) => request<Computer[]>(
        `computers?${queryKey}`, { signal: listSignal },
      );
      try {
        const result = companyId ? await withDeadline(signal, read) : await read(signal);
        if (!signal.aborted) {
          rememberFocus();
          if (companyId) {
            const membershipId = result.organizationMembershipId;
            verifiedMembership.current = membershipId
              ? { companyId, membershipId, revision: result.poolCheckRevision } : null;
          }
          setLostCompanyId((id) => id === companyId ? null : id);
        }
        return { ...result, queryKey };
      } catch (error) {
        if (!signal.aborted) {
          rememberFocus();
          if (error instanceof ApiError && [401, 403, 404].includes(error.status)) {
            if (companyId && (error.status === 403 || error.status === 404)) {
              const membershipId = error.status === 403 ? error.organizationMembershipId : undefined;
              verifiedMembership.current = membershipId
                ? { companyId, membershipId, revision: error.poolCheckRevision ?? verifiedMembership.current?.revision } : null;
              setLostCompanyId((id) => error.status === 404 ? companyId : id === companyId ? null : id);
            }
            state.update(null);
          }
        }
        throw error;
      }
    },
    queryKey,
    true,
    { retainError: true },
  );
  useReloadOnOrganizationChange(viewer, companyId, (change) => {
    if (change.type === "renamed") company.reload();
    else {
      const membership = verifiedMembership.current;
      if (change.type === "removed" && (!change.membershipId ||
          membership?.companyId === companyId && membership?.membershipId === change.membershipId)) {
        rememberFocus();
        verifiedMembership.current = null;
        setLostCompanyId(companyId);
        state.update(null);
      }
      state.reload();
    }
  });
  useReloadOnComputerPoolChange(viewer, ({ computerId, assignment }) => {
    if (!assignment || (!companyId && userId !== viewer)) {
      state.reload();
      return;
    }
    const member = verifiedMembership.current;
    const checksOrganization = !!companyId && companyId === assignment.companyId && !!assignment.membershipId;
    const newerAccess = checksOrganization && (member?.companyId !== companyId || member.revision === undefined ||
      member.revision < assignment.revision);
    if (newerAccess) {
      verifiedMembership.current = { companyId, membershipId: assignment.membershipId!, revision: assignment.revision };
    }
    const hidesOrganization = newerAccess && !assignment.canRead;
    const leavesScope = assignment.companyId !== companyId || !companyId && (!assignment.canRead || assignment.assigned === false);
    if (hidesOrganization || leavesScope) {
      rememberFocus();
      state.update((current) => {
        if (!current || current.queryKey !== queryKey ||
            current.poolCheckRevision !== undefined && current.poolCheckRevision >= assignment.revision) return current;
        if (hidesOrganization) return null;
        const remaining = current.data.filter((computer) => computer.id !== computerId);
        return remaining.length === current.data.length ? current : {
          ...current,
          data: remaining,
          total: current.total === null ? null : Math.max(0, current.total - 1),
        };
      });
    }
    state.reload();
  }, companyId);
  useRefreshWhileVisible(state.reload);
  // A response from the preceding page must not correct a newer URL.
  const data = state.data?.queryKey === queryKey ? state.data : null;
  useLayoutEffect(() => {
    const previous = savedFocus.current;
    savedFocus.current = null;
    if (previous?.key !== queryKey) return;
    const { element } = previous;
    if (element.isConnected && !element.matches(":disabled")) return;
    const focused = document.activeElement;
    if (focused !== document.body && focused !== element) return;
    const fallback = pagination.current?.contains(element) ? pagination.current : title.current;
    fallback?.focus();
  }, [data, state.error, queryKey]);
  const updatePage = (value: number) => {
    const next = new URLSearchParams(params);
    next.set("page", String(value));
    setParams(next, { state: location.state });
  };
  const totalPages = Math.max(1, Math.ceil((data?.total || 0) / 10));
  const outsidePages = !!data && page > totalPages;
  const previousPage = useRef(page);
  const firstRow = useRef<HTMLTableRowElement>(null);
  useEffect(() => {
    if (!state.loading && !state.error && outsidePages) {
      const next = new URLSearchParams(params);
      next.set("page", String(totalPages));
      navigate({ search: next.toString(), hash: location.hash }, {
        replace: true,
        state: location.hash === "#main" ? { ...location.state, keepScroll: true } : location.state,
      });
    }
  }, [state.loading, state.error, outsidePages, totalPages, params, navigate, location.hash, location.state]);
  useEffect(() => {
    if (!data || outsidePages || previousPage.current === page) return;
    previousPage.current = page;
    if (location.hash === "#main" && document.activeElement === document.getElementById("main")) return;
    const frame = requestAnimationFrame(() => {
      firstRow.current?.scrollIntoView({ block: "start" });
    });
    return () => cancelAnimationFrame(frame);
  }, [page, data, outsidePages, location.hash]);
  const lostCompany = companyId !== null && lostCompanyId === companyId;
  const label = companyId
    ? (!lostCompany && company.data?.find((c) => c.id === companyId)?.name) || t("organizations")
    : t("personal");
  const backPath = companyId ? (lostCompany ? `/organizations${directorySearch}` : organizationPath) : poolPath(null);
  const backLabel = companyId ? (lostCompany ? "backOrganizations" : "backOrganization") : "backPersonalPool";
  return (
    <>
      <Link
        className="back-link"
        to={showAll ? backPath : "/compute"}
        state={showAll ? {
          directorySearch: companyId && !lostCompany ? directorySearch : undefined,
          connectedPoolOrigin,
        } : undefined}
      >
        ← {t(showAll ? backLabel : "backCompute")}
      </Link>
      <Heading index={`02 / MUTATING COMPUTE / ${label}`} title={t("computers")} titleRef={title}>
        {t(showAll ? (companyId ? "allOrganizationComputers" : "allPersonalComputers") : "onlineOnly")}
      </Heading>
      <ErrorBox error={state.error} retry={state.reload} retryRef={retry} />
      {(!state.error && (outsidePages || !data)) ? (
        <Loading />
      ) : (
        data && !outsidePages && (
          <>
            <section className="table-panel" aria-busy={state.loading}>
              <div className="table-scroll">
                <table ref={table} className="computer-table">
                  <thead>
                    <tr>
                      <th>{t("computer")}</th>
                      <th>{t("status")}</th>
                      <th>
                        CPU · {t("free")} / {t("total")}
                      </th>
                      <th>
                        {t("memory")} · {t("free")} / {t("total")}
                      </th>
                      <th>{t("benchmarks")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.data.map((computer, index) => {
                      const to = `/compute/computers/${computer.id}?${params}`;
                      const { status, available } = computerAvailability(computer);
                      const statusContent = (
                        <>
                          <span
                            className={status === "online" ? "signal-dot" : "signal-dot inactive"}
                          />
                          {t(status)}
                        </>
                      );
                      return (
                        <tr
                          key={computer.id}
                          ref={index === 0 ? firstRow : undefined}
                          className="computer-row"
                          onClick={(event) => {
                            if (
                              !(event.target as HTMLElement).closest(
                                "a, button",
                              ) &&
                              !window.getSelection()?.toString()
                            )
                              navigate(to, { state: navigationState });
                          }}
                        >
                          <td>
                            <div className="computer-identity">
                              <Link className="computer-link" to={to} state={navigationState}>
                                <strong>
                                  {computer.name || computer.id.slice(0, 8)}
                                </strong>
                              </Link>
                              <span className="status-badge mobile-status">
                                {statusContent}
                              </span>
                            </div>
                            <small>
                              {computer.os} · {computer.architecture} ·{" "}
                              {runtimeLabel(computer.runtime)}
                            </small>
                          </td>
                          <td>
                            <span className="status-badge">
                              {statusContent}
                            </span>
                          </td>
                          <td
                            className="numeric"
                            data-label={`CPU · ${t("free")} / ${t("total")}`}
                          >
                            {available
                              ? number(computer.free_cpu / 1000, locale)
                              : "—"}{" "}
                            <span>
                              / {number(computer.cpu_millis / 1000, locale)}
                            </span>
                          </td>
                          <td
                            className="numeric"
                            data-label={`${t("memory")} · ${t("free")} / ${t("total")}`}
                          >
                            {available
                              ? number(computer.free_memory / GiB, locale)
                              : "—"}{" "}
                            <span>
                              / {number(computer.memory_bytes / GiB, locale)}{" "}
                              GiB
                            </span>
                          </td>
                          <td
                            className="computer-benchmarks"
                            data-label={t("benchmarks")}
                          >
                            <BenchmarkSummary
                              computerId={computer.id}
                              to={to}
                            />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              {!data.data.length && (
                <Empty
                  icon="computer"
                  title={t(showAll ? (companyId ? "noOrganizationComputers" : "noPersonalComputers") : "noComputers")}
                >
                  {!showAll && t("noComputersLead")}
                </Empty>
              )}
              <nav ref={pagination} className="pagination" aria-label={t("page")} tabIndex={-1}>
                <span>
                  {t("page")} {page} {t("of")} {totalPages}
                </span>
                <div>
                  <button
                    className="button secondary compact"
                    onClick={() => updatePage(page - 1)}
                    disabled={page <= 1}
                  >
                    ← {t("previous")}
                  </button>
                  <button
                    className="button secondary compact"
                    onClick={() => updatePage(page + 1)}
                    disabled={page >= totalPages}
                  >
                    {t("next")} →
                  </button>
                </div>
              </nav>
            </section>
            {!showAll && <p className="footnote">{t("resourcesNote")}</p>}
          </>
        )
      )}
    </>
  );
}
