import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate, useSearchParams, type Location } from "react-router-dom";
import { MoveComputerDialog } from "../components/MoveComputerDialog";
import { usePoolPointerLayout } from "../components/OrganizationResources";
import { Empty, ErrorBox, Heading, Icon, Loading } from "../components/UI";
import { api, ApiError, request, withDeadline } from "../lib/api";
import {
  changeComputerPool, organizationPoolLabels, poolLabel, PoolMoveCheckError, poolPath, reconcileComputerPoolMove, revealFocusedComputeControl,
  useComputerPoolMoves, useReloadOnComputerPoolChange, type ConnectedPoolOrigin, type PoolMove,
} from "../lib/compute";
import { useI18n } from "../lib/i18n";
import { runtimeLabel } from "../lib/runtime";
import { useReloadOnOrganizationChange } from "../lib/useOrganizationDirectory";
import { useSession } from "../lib/session";
import type { ConnectedComputer, ConnectedComputerPage } from "../lib/types";
import { useData, useRefreshWhileVisible } from "../lib/useData";
import { Computers } from "./Computers";

const PAGE_SIZE = 10;

type ConnectedReturn = {
  pendingPoolReturn: ConnectedPoolOrigin | null;
  onPoolReturnFinished: () => void;
};

export function connectedListEntry(location: Location) {
  if (!/^\/compute\/computers\/?$/i.test(location.pathname)) return undefined;
  const params = new URLSearchParams(location.search);
  if (params.has("company_id") || params.has("user_id") || params.has("only_available")) return undefined;
  const entry = location.state?.connectedPoolListEntry;
  return typeof entry === "string" ? entry : location.key;
}

export function ComputeComputers(connectedReturn: ConnectedReturn) {
  const sourceEntry = connectedListEntry(useLocation());
  return sourceEntry === undefined ? <Computers /> : <ConnectedComputers {...connectedReturn} sourceEntry={sourceEntry} />;
}

function ConnectedComputers({ pendingPoolReturn, onPoolReturnFinished, sourceEntry }: ConnectedReturn & { sourceEntry: string }) {
  const userId = useSession().user!.id;
  const moveFor = useComputerPoolMoves(userId);
  const { t, locale } = useI18n();
  const [params] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const originComputerId = pendingPoolReturn?.userId === userId ? pendingPoolReturn.computerId : null;
  const requestedPage = Number(params.get("page") || "1");
  const page = Number.isSafeInteger(requestedPage) && requestedPage > 0 ? Math.min(requestedPage, 1_000_000) : 1;
  const offset = (page - 1) * PAGE_SIZE;
  const key = `${userId}/${page}`;
  const list = useRef<HTMLDivElement>(null);
  const pagination = useRef<HTMLElement>(null);
  const listRetry = useRef<HTMLButtonElement>(null);
  const returningComputer = useRef<string | null>(null);
  const cancelReturn = useCallback(() => {
    returningComputer.current = null;
    onPoolReturnFinished();
  }, [onPoolReturnFinished]);
  const paginationFocus = useRef<{ key: string; element: HTMLElement } | null>(null);
  const rowFocus = useRef<{ key: string; computerId: string; control: string; element: Element } | null>(null);
  const rememberListFocus = () => {
    const element = document.activeElement;
    if (!(element instanceof HTMLElement) || !list.current?.contains(element)) return;
    if (pagination.current?.contains(element)) {
      paginationFocus.current = { key, element };
      return;
    }
    const row = element.closest<HTMLElement>("[data-computer-id]");
    const computerId = row?.dataset.computerId;
    const control = element.dataset.computerControl;
    if (computerId && (control || element === row)) {
      rowFocus.current = { key, computerId, control: control ?? "", element };
    }
  };
  const state = useData(async (signal) => {
    const snapshot = await request<ConnectedComputerPage>(`connected-computers?limit=${PAGE_SIZE}&offset=${offset}`, { signal });
    if (!signal.aborted) {
      rememberListFocus();
      if (document.activeElement === listRetry.current) title.current?.focus();
    }
    return { key, page: snapshot.data, revision: snapshot.poolCheckRevision, updatingPools: new Map<string, "pool" | "revision">() };
  }, key, true, { retainError: true });
  useRefreshWhileVisible(state.reload);
  const snapshot = state.data?.key === key ? state.data : null;
  const data = snapshot?.page ?? null;
  const [held, setHeld] = useState<{
    key: string;
    page: ConnectedComputerPage;
    error: unknown;
    feedback: { movedName?: string; error: unknown };
  } | null>(null);
  const currentGesture = held?.key === key ? held : null;
  const heldPage = currentGesture?.page ?? null;
  const denied = state.error instanceof ApiError && state.error.status >= 400 && state.error.status < 500;
  const visibleError = denied || !currentGesture ? state.error : currentGesture.error;
  const visiblePage = heldPage ?? data;
  const pages = Math.max(1, Math.ceil((visiblePage?.total ?? 0) / PAGE_SIZE));
  const outside = !heldPage && data !== null && page > pages;
  const [moving, setMoving] = useState<{ key: string; computer: ConnectedComputer } | null>(null);
  const [moved, setMoved] = useState<{ key: string; name: string } | null>(null);
  const [checkingResult, setCheckingResult] = useState(false);
  const [resultError, setResultError] = useState<{ key: string; move: PoolMove; error: unknown } | null>(null);
  const failedMove = resultError?.key === key && moveFor(resultError.move.computerId)?.idempotencyKey === resultError.move.idempotencyKey
    ? resultError : null;
  const moveFeedback = { movedName: moved?.key === key ? moved.name : undefined, error: failedMove?.error };
  const visibleFeedback = currentGesture?.feedback ?? moveFeedback;
  const holdPoolLink = usePoolPointerLayout((control) => {
    if (control.dataset.computerControl !== "pool" || !data) return;
    rememberListFocus();
    setHeld({ key, page: data, error: state.error, feedback: moveFeedback });
    return () => setHeld(null);
  });
  const retryControl = useRef<{ key: string; button: HTMLButtonElement } | null>(null);
  const currentMove = moving?.key === key ? moving : null;
  const listedComputer = data?.items.find((item) => item.id === currentMove?.computer.id);
  const offPageId = data && currentMove && !listedComputer ? currentMove.computer.id : null;
  const lookupKey = `${key}/${offPageId ?? ""}`;
  const lookup = useData(async (signal) => {
    if (!offPageId) return null;
    const result = await request<ConnectedComputerPage>(`connected-computers?computer_id=${encodeURIComponent(offPageId)}`, { signal });
    return { key: lookupKey, computer: result.data.items.find((item) => item.id === offPageId), revision: result.poolCheckRevision };
  }, lookupKey, true);
  const lookedUp = offPageId && lookup.data?.key === lookupKey ? lookup.data : null;
  const currentComputer = listedComputer ?? lookedUp?.computer;
  const lookupError = offPageId ? lookup.error : null;
  const lookingUpComputer = Boolean(offPageId && (lookup.loading || (!lookedUp && !lookupError)));
  useRefreshWhileVisible(lookup.reload, Boolean(offPageId));
  const reloadComputers = () => {
    state.reload();
    if (offPageId) lookup.reload();
  };
  useReloadOnOrganizationChange(userId, "all", (change) => {
    if (change.type === "removed") {
      const maskPool = (item: ConnectedComputer) => (
        item.pool?.company_id === change.companyId &&
        (!change.membershipId || !item.pool_membership_id || item.pool_membership_id === change.membershipId)
      ) ? { ...item, pool: null, pool_membership_id: null, can_move: false } : item;
      rememberListFocus();
      state.update((current) => current?.key === key ? {
        ...current,
        page: { ...current.page, items: current.page.items.map(maskPool) },
      } : current);
      if (offPageId) {
        lookup.update((current) => current?.key === lookupKey && current.computer
          ? { ...current, computer: maskPool(current.computer) } : current);
      }
    }
    reloadComputers();
  }, { includeMemberChanges: true });
  const currentAttempt = currentMove ? moveFor(currentMove.computer.id) : undefined;
  const pageToFocus = useRef<number | null>(null);
  const currentPageButton = useRef<HTMLButtonElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const number = (value: number) => value.toLocaleString(locale);
  useReloadOnComputerPoolChange(userId, ({ computerId, previousCompanyId, previousRevision, assignment, superseded, checkedRevision }) => {
    const revision = assignment?.revision ?? checkedRevision;
    const showsPreviousPool = (row: ConnectedComputer | undefined) => row?.pool &&
      row.pool.company_id === previousCompanyId &&
      (previousRevision === undefined || row.pool_revision === previousRevision);
    if (!superseded) {
      rememberListFocus();
      state.update((current) => {
        if (current?.key !== key || revision !== undefined && (current.revision ?? -1) >= revision) return current;
        const shown = current.page.items.find((item) => item.id === computerId);
        const confirmed = assignment && shown?.pool && assignment.companyId === shown.pool.company_id &&
          (assignment.companyId === null ? assignment.canRead && assignment.assigned !== false
            : assignment.membershipId === shown.pool_membership_id);
        // A confirmation can restore the destination link before the list
        // returns the ownership revision needed to make another move.
        const pending = confirmed ? "revision" : showsPreviousPool(shown) ? "pool" : undefined;
        if (!pending || current.updatingPools.get(computerId) === pending) return current;
        const updatingPools = new Map(current.updatingPools).set(computerId, pending);
        return { ...current, updatingPools };
      });
    }
    state.reload();
    if (offPageId === computerId) {
      if (!superseded) {
        lookup.update((current) => current?.key === lookupKey &&
          (revision === undefined || (current.revision ?? -1) < revision) &&
          (assignment || showsPreviousPool(current.computer)) ? null : current);
      }
      lookup.reload();
    }
  });
  const settle = async (move: PoolMove, computer: ConnectedComputer) => {
    const applied = await reconcileComputerPoolMove(move);
    if (applied) setMoved({ key, name: computer.name || computer.id.slice(0, 8) });
    return applied;
  };
  const updatePage = (value: number, replace = false) => {
    if (value === page) return;
    const next = new URLSearchParams(params);
    if (value === 1) next.delete("page");
    else next.set("page", String(value));
    const computerId = replace ? returningComputer.current : null;
    pageToFocus.current = computerId ? null : value;
    if (replace) setMoved((current) => current?.key === key ? { ...current, key: `${userId}/${value}` } : current);
    const hash = replace ? location.hash : "";
    navigate({ search: next.toString(), hash }, { replace, state: replace ? {
      connectedPoolListEntry: sourceEntry,
      connectedPoolCorrection: computerId ? { entry: sourceEntry, userId, computerId } : undefined,
      keepScroll: hash === "#main",
    } : undefined });
  };
  useEffect(() => {
    setMoving((current) => current?.key === key ? current : null);
    setMoved((current) => current?.key === key ? current : null);
  }, [key]);
  useLayoutEffect(() => {
    if (checkingResult) return;
    const origin = retryControl.current;
    retryControl.current = null;
    const focused = document.activeElement;
    if (origin?.key !== key || (focused !== document.body && focused !== origin.button)) return;
    if (origin.button.isConnected && !origin.button.disabled) origin.button.focus({ preventScroll: true });
    else title.current?.focus();
  }, [checkingResult, key]);
  useLayoutEffect(() => {
    const previous = rowFocus.current;
    const previousPager = paginationFocus.current;
    rowFocus.current = null;
    paginationFocus.current = null;
    const focused = document.activeElement;
    if (previousPager?.key === key &&
        (!previousPager.element.isConnected || previousPager.element.matches(":disabled")) &&
        (focused === document.body || focused === previousPager.element)) {
      (currentPageButton.current ?? title.current)?.focus();
      return;
    }
    if (previous?.key !== key || (previous.element.isConnected &&
        !previous.element.matches(":disabled") && !previous.element.closest('[aria-hidden="true"]'))) return;
    if (focused !== document.body && focused !== previous.element && !focused?.closest('[aria-hidden="true"]')) return;
    const row = list.current?.querySelector<HTMLElement>(`[data-computer-id="${CSS.escape(previous.computerId)}"]:not([aria-hidden="true"])`);
    const control = previous.control
      ? row?.querySelector<HTMLElement>(`[data-computer-control="${CSS.escape(previous.control)}"]:not(:disabled)`)
      : null;
    (control ?? row ?? title.current)?.focus();
  }, [snapshot, key]);
  useEffect(() => {
    if (!data || outside || pageToFocus.current !== page) return;
    if (document.activeElement === document.body) {
      const button = currentPageButton.current;
      const bounds = button?.getBoundingClientRect();
      if (bounds && bounds.top >= 0 && bounds.bottom <= window.innerHeight) {
        button?.focus({ preventScroll: true });
      } else title.current?.focus();
    }
    pageToFocus.current = null;
  }, [data, outside, page]);
  useLayoutEffect(() => {
    returningComputer.current = originComputerId;
    if (!originComputerId) return;
    const controller = new AbortController();
    for (const event of ["keydown", "pointerdown", "wheel", "touchstart"]) {
      window.addEventListener(event, cancelReturn, { capture: true, passive: true, signal: controller.signal });
    }
    return () => {
      controller.abort();
      returningComputer.current = null;
    };
  }, [originComputerId, cancelReturn]);
  useEffect(() => {
    if (!data || state.loading || state.error) return;
    const { items, total } = data;
    const controller = new AbortController();
    let frame: number | undefined;
    let retry: number | undefined;
    const computerId = returningComputer.current;
    const canRestoreFocus = () => document.activeElement === document.body ||
      document.activeElement === document.getElementById("main");

    async function restoreReturnFocus() {
      if (controller.signal.aborted) return;
      if (computerId && returningComputer.current === computerId && total > 0 && canRestoreFocus() &&
          !items.some((computer) => computer.id === computerId)) {
        let position: number | null = null;
        try {
          position = await withDeadline(controller.signal, (signal) => api<number | null>(
            `connected-computers/${encodeURIComponent(computerId)}/position`, { signal },
          ));
        } catch (error) {
          if (controller.signal.aborted || error instanceof ApiError && error.status === 401) return;
          const temporary = !(error instanceof ApiError) || error.status === 0 || error.status === 429 || error.status >= 500;
          if (temporary && returningComputer.current === computerId && canRestoreFocus() && !outside) {
            retry = window.setTimeout(() => void restoreReturnFocus(), 5_000);
            return;
          }
        }
        if (controller.signal.aborted) return;
        if (position !== null && returningComputer.current === computerId && canRestoreFocus()) {
          const target = Math.min(1_000_000, Math.floor(position / PAGE_SIZE) + 1);
          if (target !== page) {
            updatePage(target, true);
            return;
          }
        }
      }
      if (outside && !currentMove) {
        updatePage(pages, true);
        return;
      }
      if (!computerId) return;
      frame = requestAnimationFrame(() => {
        if (returningComputer.current !== computerId) return;
        cancelReturn();
        if (!canRestoreFocus()) return;
        const row = list.current?.querySelector<HTMLElement>(`[data-computer-id="${CSS.escape(computerId)}"]`);
        const link = row?.querySelector<HTMLElement>('[data-computer-control="pool"]');
        const control = link ?? row ?? title.current;
        control?.focus({ preventScroll: true });
        control?.scrollIntoView({ block: "center", behavior: "instant" });
      });
    }
    void restoreReturnFocus();
    return () => {
      controller.abort();
      window.clearTimeout(retry);
      if (frame !== undefined) cancelAnimationFrame(frame);
    };
  }, [data, state.loading, state.error, location.key, location.hash, outside, currentMove, page, pages, params, navigate]);
  const firstPage = Math.min(Math.max(1, page - 2), Math.max(1, pages - 4));
  const pageNumbers = Array.from({ length: Math.min(pages, 5) }, (_, index) => firstPage + index);
  const organizationNames = new Map<string, string>();
  for (const { pool } of visiblePage?.items ?? []) {
    if (pool?.company_id && pool.name !== null) organizationNames.set(pool.company_id, pool.name);
  }
  const poolLabels = organizationPoolLabels(Array.from(organizationNames, ([id, name]) => ({ id, name })),
    t("personalPool"), t("organizationType"));
  const groups = [
    { label: t("online"), online: true, items: visiblePage?.items.filter((item) => item.online) ?? [] },
    { label: t("connectionOffline"), online: false, items: visiblePage?.items.filter((item) => !item.online) ?? [] },
  ];
  return (
    <>
      <Link className="back-link compute-back-link" to="/compute" onFocus={revealFocusedComputeControl}>← {t("backCompute")}</Link>
      <Heading title={t("connectedComputers")} titleRef={title}>{t("connectedComputersLead")}</Heading>
      {visibleFeedback.movedName && <p className="success-message" role="status">{visibleFeedback.movedName} · {t("changedPool")}</p>}
      <ErrorBox error={visibleError} retry={state.reload} retryRef={listRetry} />
      <ErrorBox error={visibleFeedback.error} />
      {outside || state.loading && !visiblePage ? <Loading /> : visiblePage && (
        visiblePage.total === 0 ? (
          <Empty icon="computer" title={t("noComputers")}>{t("connectedComputersEmpty")}</Empty>
        ) : (
          <div ref={list} className="connected-computers" aria-busy={state.loading} onFocus={revealFocusedComputeControl}
            onPointerDownCapture={holdPoolLink}>
            {groups.map(({ label, online, items }) => items.length > 0 && (
              <section className="connected-computer-group" key={label} aria-label={label}>
                <h2>{label}</h2>
                <ul>
                  {items.map((computer) => {
                    const live = heldPage ? data?.items.find((item) => item.id === computer.id) : computer;
                    const pool = live ? live.pool : computer.pool;
                    const poolName = pool?.company_id === computer.pool?.company_id ? computer.pool?.name : pool?.name;
                    const name = computer.name || computer.id.slice(0, 8);
                    const attempt = moveFor(computer.id);
                    const poolUpdate = state.data?.updatingPools.get(computer.id);
                    const updatingPool = attempt?.unsettled || poolUpdate === "pool";
                    return (
                      <li className="connected-computer-row" key={computer.id} data-computer-id={computer.id} tabIndex={-1}
                        aria-hidden={!live || undefined} style={live ? undefined : { visibility: "hidden" }}>
                        <div className="connected-computer-name">
                          <Icon name="computer" size={17} />
                          <div>
                            <strong>{name}</strong>
                            <small>{computer.os} · {computer.architecture} · {runtimeLabel(computer.runtime)}</small>
                          </div>
                        </div>
                        <div className="connected-computer-pool">
                          <small>{t("currentPool")}</small>
                          {updatingPool ? <span>{t("poolUpdating")}</span> : pool ? (
                            <Link className="connected-pool-link" data-computer-control="pool"
                              to={pool.company_id === null ? poolPath(null) : `/organizations/${encodeURIComponent(pool.company_id)}`}
                              state={{ connectedPoolOrigin: { entry: sourceEntry, userId, computerId: computer.id } }}>
                              {(pool.company_id && poolLabels.get(pool.company_id)) ??
                                poolLabel(poolName ?? null, t("personalPool"), t("organizationType"))}
                            </Link>
                          ) : <span>{t("unavailablePool")}</span>}
                          {computer.transfer_pending && <small>{t("computerTransferPending")}</small>}
                        </div>
                        <span className="status-badge">
                          <span className={online ? "signal-dot" : "signal-dot inactive"} /> {label}
                        </span>
                        {attempt?.unsettled ? <button
                          type="button"
                          className="button secondary compact connected-move"
                          data-computer-control="move"
                          aria-label={`${t("checkPoolChange")} — ${name}`}
                          disabled={checkingResult}
                          onClick={async (event) => {
                            const button = event.currentTarget;
                            button.focus({ preventScroll: true });
                            retryControl.current = { key, button };
                            setCheckingResult(true);
                            setResultError(null);
                            try { await settle(attempt, computer); }
                            catch (error) { setResultError({ key, move: attempt, error: error instanceof PoolMoveCheckError ? error.cause : error }); }
                            finally { setCheckingResult(false); }
                          }}
                        >{t("checkPoolChange")}</button> : <button
                          type="button"
                          className="button secondary compact connected-move"
                          data-computer-control="move"
                          aria-label={`${t("changePool")} — ${name}`}
                          disabled={Boolean(attempt) || poolUpdate !== undefined || !live?.can_move}
                          onClick={(event) => {
                            event.currentTarget.focus({ preventScroll: true });
                            setMoved(null);
                            setMoving({ key, computer: live ?? computer });
                          }}
                        >
                          {t("changePool")}
                        </button>}
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))}
            <nav ref={pagination} className="pagination connected-pagination" aria-label={t("page")}>
              <span aria-live="polite">{number(offset + 1)}–{number(offset + visiblePage.items.length)} {t("of")} {number(visiblePage.total)}</span>
              <div>
                <button className="button secondary compact" aria-label={t("previous")} disabled={page <= 1} onClick={() => updatePage(page - 1)}>‹</button>
                {pageNumbers.map((value) => (
                  <button
                    key={value}
                    className="button secondary compact"
                    aria-label={`${t("page")} ${number(value)}`}
                    aria-current={page === value ? "page" : undefined}
                    ref={page === value ? currentPageButton : undefined}
                    onClick={() => updatePage(value)}
                  >
                    {number(value)}
                  </button>
                ))}
                <button className="button secondary compact" aria-label={t("next")} disabled={page >= pages} onClick={() => updatePage(page + 1)}>›</button>
              </div>
            </nav>
          </div>
        )
      )}
      {currentMove && (
        <MoveComputerDialog
          computer={currentMove.computer}
          currentComputer={currentComputer}
          checkingConnection={offPageId ? lookingUpComputer : state.loading}
          connectionError={offPageId ? lookupError : state.error}
          poolUpdating={Boolean(currentAttempt?.unsettled || state.data?.updatingPools.has(currentMove.computer.id))}
          retryKey={currentAttempt?.unsettled ? currentAttempt.idempotencyKey : undefined}
          onClose={() => setMoving((current) => current === currentMove ? null : current)}
          restoreFocus={() => title.current?.focus()}
          reloadComputers={reloadComputers}
          retryMove={currentAttempt?.unsettled ? () => settle(currentAttempt, currentMove.computer) : undefined}
          onMove={async (companyId) => {
            const computer = currentMove.computer;
            const previousCompanyId = computer.pool?.company_id ?? null;
            if (!await changeComputerPool({ userId, computerId: computer.id, previousCompanyId, previousRevision: computer.pool_revision }, companyId)) {
              throw new ApiError(409, "Computer pool change was cancelled");
            }
            setMoved({ key, name: computer.name || computer.id.slice(0, 8) });
          }}
        />
      )}
    </>
  );
}
