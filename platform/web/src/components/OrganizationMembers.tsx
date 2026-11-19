import { useCallback, useEffect, useId, useRef, useState, type FocusEvent, type RefObject } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { api, ApiError, json, request, withDeadline } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { organizationPage } from "../lib/organizations";
import { memberName } from "../lib/permissions";
import { useSession } from "../lib/session";
import type { Company, Membership } from "../lib/types";
import { useData } from "../lib/useData";
import { ApiImage } from "./ApiImage";
import { PermissionSummary } from "./InvitationUI";
import { MemberEditor } from "./MemberEditor";
import { ConfirmDialog, ErrorBox, Icon, Loading, useRestoreFocusOnRemoval } from "./UI";

const previewSize = 5;
export const memberDirectoryPageSize = 20;
const searchLength = 201;

function memberSearch(value: string) {
  return Array.from(value.replace(/\0/g, "")).slice(0, searchLength).join("");
}

export function useMemberSearch(drafts: Map<string, string>) {
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const fromUrl = memberSearch(params.get("q") ?? "");
  const [draft, setDraft] = useState<{ entry: string; value: string }>();
  const input = draft?.entry === location.key ? draft.value : drafts.get(location.key) ?? fromUrl;
  useEffect(() => {
    if (input === fromUrl) return;
    const timer = window.setTimeout(() => {
      // The router can still be rendering the previous entry after browser Back.
      if ((window.history.state?.key ?? "default") !== location.key || drafts.get(location.key) !== input) return;
      const next = new URLSearchParams(params);
      if (input) next.set("q", input);
      else next.delete("q");
      if (input.trim() !== fromUrl.trim()) next.delete("page");
      drafts.delete(location.key);
      setParams(next, { replace: true, state: { ...location.state, keepScroll: true } });
    }, 240);
    return () => window.clearTimeout(timer);
  }, [input, fromUrl, drafts, params, setParams, location.key, location.state]);

  return {
    input,
    fromUrl,
    change(value: string) {
      const search = memberSearch(value);
      if (search === fromUrl) drafts.delete(location.key);
      else drafts.set(location.key, search);
      setDraft({ entry: location.key, value: search });
    },
  };
}

export type MemberSearch = ReturnType<typeof useMemberSearch>;

function scrollOrganizationControlIntoView(control: HTMLElement) {
  const header = document.querySelector(".site-header");
  const { top, bottom } = control.getBoundingClientRect();
  const behindHeader = top < (header?.getBoundingClientRect().bottom ?? 0);
  if (behindHeader || bottom > window.innerHeight) {
    control.scrollIntoView({ block: behindHeader ? "start" : "nearest", inline: "nearest", behavior: "instant" });
  }
}

export function keepOrganizationControlVisible(event: FocusEvent<HTMLElement>) {
  const control = event.target;
  if (!control.matches(":is(a, button, input, select, summary):focus-visible")) return;
  requestAnimationFrame(() => {
    if (document.activeElement !== control || !control.matches(":focus-visible")) return;
    scrollOrganizationControlIntoView(control);
  });
}

function keepRestoredRosterFocusVisible(control: HTMLElement) {
  const inventory = control.closest(".organization-detail")?.querySelector('.organization-computers[aria-busy="true"]');
  if (!inventory) return;
  const controller = new AbortController();
  const stop = () => controller.abort();
  const options = { capture: true, signal: controller.signal };
  let frame = 0;
  // Loading computers can shift the restored target before scroll anchoring applies.
  const observer = new MutationObserver(() => {
    if (inventory.getAttribute("aria-busy") === "true") return;
    observer.disconnect();
    frame = requestAnimationFrame(() => {
      if (document.activeElement === control) scrollOrganizationControlIntoView(control);
      stop();
    });
  });
  observer.observe(inventory, { attributes: true, attributeFilter: ["aria-busy"] });
  for (const event of ["pointerdown", "keydown", "wheel"]) window.addEventListener(event, stop, options);
  control.addEventListener("blur", stop, options);
  controller.signal.addEventListener("abort", () => {
    observer.disconnect();
    cancelAnimationFrame(frame);
  }, { once: true });
  return stop;
}

function MemberDirectoryLink({ companyId, input, count, linkRef, restoreFocus }: {
  companyId: string;
  input: string;
  count: number;
  linkRef: RefObject<HTMLAnchorElement | null>;
  restoreFocus: () => void;
}) {
  const { t, locale } = useI18n();
  const location = useLocation();
  useRestoreFocusOnRemoval(linkRef, restoreFocus);

  const search = input.trim() ? `?${new URLSearchParams({ q: input })}` : "";
  return <div className="organization-member-list-link">
    <Link ref={linkRef} to={{ pathname: `/organizations/${companyId}/members`, search }}
      state={{ ...location.state, memberSearch: input }}>
      <span>{search ? t("memberResultsLink") : t("allMembersLink").replace("{count}", count.toLocaleString(locale))}</span>
      <Icon name="arrow" size={16} />
    </Link>
  </div>;
}

function MemberDirectoryPagination({ page, pages, pagerRef, onPage, restoreFocus }: {
  page: number;
  pages: number;
  pagerRef: RefObject<HTMLElement | null>;
  onPage: (value: number) => void;
  restoreFocus: () => void;
}) {
  const { t, locale } = useI18n();
  useRestoreFocusOnRemoval(pagerRef, restoreFocus);
  return <nav ref={pagerRef} className="member-directory-pagination" aria-label={t("page")}>
    <button type="button" className="button secondary compact" disabled={page <= 1}
      onClick={() => onPage(page - 1)}>← {t("previous")}</button>
    <form key={page} onSubmit={(event) => {
      event.preventDefault();
      const next = Number(new FormData(event.currentTarget).get("page"));
      if (Number.isInteger(next) && next >= 1 && next <= pages && next !== page) onPage(next);
    }}>
      <label>
        <span>{t("page")}</span>
        <input type="number" name="page" min={1} max={pages} defaultValue={page} required aria-label={t("memberListPage")} />
        <span>{t("of")} {pages.toLocaleString(locale)}</span>
      </label>
      <button className="button secondary compact">{t("goToPage")}</button>
    </form>
    <button type="button" className="button secondary compact" disabled={page >= pages}
      onClick={() => onPage(page + 1)}>{t("next")} →</button>
  </nav>;
}

type MemberAction = "edit" | "remove";

function MemberRow({ member, detailsId, expanded, editable, removable, onToggle, onAction, restoreFocus }: {
  member: Membership;
  detailsId: string;
  expanded: boolean;
  editable: boolean;
  removable: boolean;
  onToggle: () => void;
  onAction: (action: MemberAction) => void;
  restoreFocus: () => void;
}) {
  const { t } = useI18n();
  const row = useRef<HTMLElement>(null);
  useRestoreFocusOnRemoval(row, restoreFocus);
  const name = memberName(member.user);

  return <article ref={row} className="member-row" aria-label={name}>
    <h3 className="organization-team-row-heading">
      <button className="organization-member-summary" type="button" aria-expanded={expanded}
        aria-controls={detailsId} onClick={onToggle}>
        <span className={`avatar organization-member-avatar ${member.role}`} aria-hidden="true">
          <ApiImage path={member.user.avatar_version
            ? `memberships/${member.id}/avatar?v=${encodeURIComponent(member.user.avatar_version)}` : null}
            fallback={Array.from(name)[0]?.toUpperCase()} />
        </span>
        <span className="organization-member-name">
          <strong>{name}</strong>
          {member.user.nickname && <small>@{member.user.nickname}</small>}
        </span>
        <span className={`organization-member-role ${member.role}`}>{t(member.role)}</span>
        <span className="organization-team-chevron"><Icon name="chevron-down" size={15} /></span>
      </button>
    </h3>
    <div className="organization-member-permissions" id={detailsId} hidden={!expanded}>
      <p className="eyebrow">{t("organizationPermissions")}</p>
      <PermissionSummary values={member.effective_permissions} />
      {editable && <div className="member-actions">
        <button type="button" className="button secondary compact" onClick={(event) => {
          event.currentTarget.focus({ preventScroll: true });
          onAction("edit");
        }}>{t("editMember")}</button>
        {removable && <button type="button" className="text-button danger-text" onClick={(event) => {
          event.currentTarget.focus({ preventScroll: true });
          onAction("remove");
        }}>{t("removeMember")}</button>}
      </div>}
    </div>
  </article>;
}

function MemberSearchField({ search, inputRef, restoreFocus }: {
  search: MemberSearch;
  inputRef: RefObject<HTMLInputElement | null>;
  restoreFocus: () => void;
}) {
  const { t } = useI18n();
  useRestoreFocusOnRemoval(inputRef, restoreFocus);
  return <label className="organization-member-search">
    <Icon name="search" size={16} />
    <input ref={inputRef} type="search" value={search.input} maxLength={searchLength * 2}
      onChange={(event) => search.change(event.currentTarget.value)}
      aria-label={t("searchMembers")} placeholder={t("searchMembers")} />
  </label>;
}

function MemberLoadError({ error, retry, restoreFocus }: {
  error: unknown;
  retry: () => void;
  restoreFocus: () => void;
}) {
  const alert = useRef<HTMLDivElement>(null);
  useRestoreFocusOnRemoval(alert, restoreFocus);
  return <div ref={alert}><ErrorBox error={error} retry={retry} /></div>;
}

export function OrganizationMembers({
  company,
  memberSearch,
  directory = false,
  onReturn,
  revision = 0,
  check,
  onCount,
  knownCount,
  onChanged,
  onAccessError,
  onMembershipChanged,
}: {
  company: Company;
  memberSearch: MemberSearch;
  directory?: boolean;
  onReturn?: () => void;
  revision?: number;
  check?: { error: unknown; retry: () => void };
  onCount?: (count: number) => void;
  knownCount?: number | null;
  onChanged: () => void;
  onAccessError: () => void;
  onMembershipChanged: (member: Membership) => void;
}) {
  const { t, locale } = useI18n();
  const { user } = useSession();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const { input, fromUrl: urlInput } = memberSearch;
  const query = urlInput.trim();
  const pendingSearch = input.trim() !== query;
  const page = directory ? organizationPage(params) : 1;
  const limit = directory ? memberDirectoryPageSize : previewSize;
  const offset = (page - 1) * limit;
  const canRead = company.my_permissions.includes("members.read");
  const canManage = company.my_permissions.includes("members.manage");
  const requestParams = new URLSearchParams({ company_id: company.id, limit: String(limit), offset: String(offset) });
  if (query) requestParams.set("q", query);
  const key = `${requestParams}/${company.my_membership_id}/${company.my_role}/${company.my_permissions.join(",")}`;
  const state = useData(async (signal) => ({
    ...(canRead ? await withDeadline(signal, (panelSignal) =>
      request<Membership[]>(`memberships?${requestParams}`, { signal: panelSignal }),
    ) : { data: [], total: 0 }),
    key,
  }), key, true, revision);
  const result = state.data?.key === key ? state.data : null;
  const total = result?.total ?? result?.data.length ?? 0;
  const currentCount = result ? result.unfilteredTotal ?? (query ? null : result.total) : null;
  const count = currentCount ?? knownCount ?? company.member_count;
  const pages = Math.max(1, Math.ceil(total / limit));
  const outsidePages = !!result && page > pages;
  const settled = !canRead || !pendingSearch && (!!state.error || !!result && !outsidePages);
  const accessError = state.error instanceof ApiError && (state.error.status === 403 || state.error.status === 404);
  const waitingForResults = canRead && (pendingSearch || !state.error && (!result || outsidePages));
  const previewResolved = result !== null || knownCount != null || !!state.error;
  const showFullList = !directory && canRead && !accessError && previewResolved && count > previewSize;
  const number = (value: number) => value.toLocaleString(locale);
  const [expanded, setExpanded] = useState(() => new Set<string>());
  const [action, setAction] = useState<{ type: MemberAction; member: Membership; scope: string } | null>(null);
  const canEditMember = (role: Membership["role"]) => canManage && (company.my_role === "creator" || role === "user");
  const actionRole = action && (result?.data.find((member) => member.id === action.member.id)?.role ?? action.member.role);
  const activeAction = action?.scope === key && canRead && actionRole && canEditMember(actionRole) ? action : null;
  const rowsId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const panel = useRef<HTMLElement>(null);
  const pagination = useRef<HTMLElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const fullList = useRef<HTMLAnchorElement>(null);
  const stopReturnScroll = useRef<(() => void) | undefined>(undefined);
  const previousPage = useRef(page);

  const focusRoster = useCallback(() => {
    (search.current ?? panel.current)?.focus({ preventScroll: true });
  }, []);

  const restoreRosterFocus = useCallback(() => {
    if (document.activeElement === document.body) focusRoster();
  }, [focusRoster]);

  function updatePage(value: number, replace = false) {
    if (pagination.current?.contains(document.activeElement)) {
      focusRoster();
      panel.current?.scrollIntoView({ block: "start", behavior: "instant" });
    }
    const next = new URLSearchParams(params);
    if (value > 1) next.set("page", String(value));
    else next.delete("page");
    setParams(next, { replace, state: { ...location.state, keepScroll: true } });
  }

  useEffect(() => {
    if (action && !activeAction) setAction((current) => current === action ? null : current);
  }, [action, activeAction]);

  useEffect(() => {
    if (currentCount !== null && canRead) onCount?.(currentCount);
  }, [currentCount, canRead, onCount]);

  useEffect(() => {
    if (accessError) onAccessError();
  }, [accessError, onAccessError]);

  useEffect(() => {
    if (outsidePages && input === urlInput && !state.loading && !state.error) updatePage(pages, true);
  }, [outsidePages, input, urlInput, state.loading, state.error, pages, params]);

  useEffect(() => {
    if (!directory) return;
    const frame = requestAnimationFrame(() => {
      if (document.activeElement === document.body) heading.current?.focus({ preventScroll: true });
    });
    return () => cancelAnimationFrame(frame);
  }, [directory]);

  useEffect(() => {
    if (!settled || state.loading || previousPage.current === page) return;
    const frame = requestAnimationFrame(() => {
      previousPage.current = page;
      const focused = document.activeElement;
      if (focused === document.body || focused === panel.current) focusRoster();
      else if (!panel.current?.contains(focused)) return;
      panel.current?.scrollIntoView({ block: "start", behavior: "instant" });
    });
    return () => cancelAnimationFrame(frame);
  }, [settled, state.loading, page, focusRoster]);

  useEffect(() => {
    if (!onReturn || !settled) return;
    const frame = requestAnimationFrame(() => {
      onReturn();
      if (document.activeElement !== document.body) return;
      const control = fullList.current ?? search.current ?? panel.current;
      control?.focus({ preventScroll: true });
      control?.scrollIntoView({ block: "center", behavior: "instant" });
      stopReturnScroll.current?.();
      if (control) stopReturnScroll.current = keepRestoredRosterFocusVisible(control);
    });
    return () => cancelAnimationFrame(frame);
  }, [onReturn, settled]);

  useEffect(() => () => stopReturnScroll.current?.(), []);

  function toggle(id: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  const results = (
    <section ref={panel} className={`members-panel${directory ? " organization-widget member-directory-panel" : ""}`}
      aria-label={t("members")} tabIndex={-1} onFocus={directory ? keepOrganizationControlVisible : undefined}>
      {canRead && <MemberSearchField search={memberSearch} inputRef={search} restoreFocus={restoreRosterFocus} />}
      <div className="organization-member-results" role="group" aria-label={t("memberSearchResults")}
        tabIndex={waitingForResults && !showFullList ? 0 : -1}>
        {!canRead ? <p className="muted">{t("memberReadDenied")}</p> : waitingForResults ? <Loading /> : state.error ? (
          <MemberLoadError error={state.error} restoreFocus={restoreRosterFocus} retry={() => {
            focusRoster();
            state.reload();
          }} />
        ) : result && (
          <>
            {directory && !!result.data.length && <p className="member-directory-range" role="status">
              {number(offset + 1)}{result.data.length > 1 && `–${number(offset + result.data.length)}`} {t("of")} {number(total)}
            </p>}
            {result.data.length ? <div className="organization-team-list" aria-busy={state.loading || undefined}>
              {result.data.map((member) => <MemberRow key={member.id} member={member}
                detailsId={`${rowsId}-member-${member.id}`} expanded={expanded.has(member.id)}
                editable={canEditMember(member.role)} removable={member.user_id !== user!.id}
                onToggle={() => toggle(member.id)} onAction={(type) => setAction({ type, member, scope: key })}
                restoreFocus={restoreRosterFocus} />)}
            </div> : <p className="organization-members-empty" role="status">{t("membersNoMatches")}</p>}
            {directory && pages > 1 && <MemberDirectoryPagination page={page} pages={pages}
              pagerRef={pagination} onPage={updatePage} restoreFocus={restoreRosterFocus} />}
          </>
        )}
      </div>
      {showFullList && <MemberDirectoryLink companyId={company.id} input={input} count={count}
        linkRef={fullList} restoreFocus={restoreRosterFocus} />}
    </section>
  );
  function closeAction() {
    setAction((current) => current === activeAction ? null : current);
  }

  function removeVisibleMember(id: string) {
    state.update((value) => value?.key === key ? {
      ...value,
      data: value.data.filter((member) => member.id !== id),
    } : value);
  }

  function refreshMemberAccess(missingId?: string) {
    if (missingId) {
      removeVisibleMember(missingId);
      onChanged();
    } else state.reload();
    onAccessError();
  }

  return <>
    {directory ? <div className="organization-detail member-directory">
      <header className="member-directory-heading">
        <div>
          <p className="eyebrow">{company.name}</p>
          <h1 ref={heading} tabIndex={-1}>{t("members")}<span className="hot" aria-hidden="true">.</span></h1>
        </div>
        {canRead && <span className="member-directory-count"><Icon name="users" size={17} />{t("members")} {number(count)}</span>}
      </header>
      {results}
    </div> : results}
    {activeAction?.type === "edit" && <MemberEditor
      key={activeAction.member.id} member={activeAction.member} company={company} onClose={closeAction}
      onAccessError={(status) => refreshMemberAccess(status === 404 ? activeAction.member.id : undefined)}
      restoreFocus={restoreRosterFocus}
      onSaved={(member) => {
        if (member.id === company.my_membership_id) onMembershipChanged(member);
        onChanged();
      }} />}
    {activeAction?.type === "remove" && <ConfirmDialog
      check={check} title={t("removeMember")} value={memberName(activeAction.member.user)}
      warning={t("removeMemberWarning")} conflict="memberConflict" onClose={closeAction}
      restoreFocus={restoreRosterFocus}
      onConfirm={async () => {
        const { id } = activeAction.member;
        try {
          await api(`memberships/${id}`, json("DELETE"));
        } catch (error) {
          if (error instanceof ApiError && error.status === 404) {
            refreshMemberAccess(id);
            return;
          }
          if (error instanceof ApiError && error.status === 403) refreshMemberAccess();
          throw error;
        }
        removeVisibleMember(id);
        onChanged();
      }} />}
  </>;
}
