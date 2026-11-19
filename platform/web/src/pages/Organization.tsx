import { Activity, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import { WidgetLayout } from "@mutating/ui/react";
import "@mutating/ui/styles/widget-layout.css";
import {
  Link,
  Navigate,
  useLocation,
  useMatch,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  api,
  allPages,
  ApiError,
  DEFAULT_TIMEOUT_MS,
  json,
  request,
  withDeadline,
} from "../lib/api";
import { useData } from "../lib/useData";
import { useSession } from "../lib/session";
import {
  notifyOrganizationChange,
  subscribeToOrganizationChanges,
} from "../lib/useOrganizationDirectory";
import { useI18n, type MessageKey } from "../lib/i18n";
import { organizationImagePath } from "../lib/organizationImages";
import { organizationPage } from "../lib/organizations";
import { memberName } from "../lib/permissions";
import type {
  Company,
  CompanyAppearance,
  CreatedInvitation,
  Invitation,
  Membership,
} from "../lib/types";
import { ConfirmDialog, ErrorBox, Icon, Loading, useRestoreFocusOnRemoval } from "../components/UI";
import { MemberSharing } from "../components/MemberSharing";
import { keepOrganizationControlVisible, memberDirectoryPageSize, OrganizationMembers, useMemberSearch, type MemberSearch } from "../components/OrganizationMembers";
import { ApiImage } from "../components/ApiImage";
import { SectionTabs } from "../components/SectionTabs";
import { OrganizationSettings } from "../components/OrganizationSettings";
import { OrganizationComputers, OrganizationOverview, OrganizationResourceCharts, type OrganizationResourceAccess } from "../components/OrganizationResources";
import organizationArtwork from "../assets/organization-atlas.webp";
import {
  InvitationExpiry,
  InvitationFacts,
  PermissionSummary,
  ShareLink,
} from "../components/InvitationUI";
import "../styles/organization.css";

type CompanyCheck = { view: symbol; reason: "access" | "update" };
type InvitationAction = "link" | "revoke";

function useTeamPointerLayout() {
  const gesture = useRef<AbortController | null>(null);
  useEffect(() => () => gesture.current?.abort(), []);

  return (event: ReactPointerEvent<HTMLElement>) => {
    if (!event.isTrusted || !event.isPrimary || event.button !== 0 || !(event.target instanceof Element)) return;
    const control = event.target.closest<HTMLElement>("a[href], button, input, select, textarea, summary, .tmc-select");
    if (!control) return;
    const openSelect = event.target.closest(".tmc-select__panel");
    gesture.current?.abort();
    const detail = event.currentTarget.closest(".organization-detail");
    const sections = detail?.querySelectorAll<HTMLElement>(
      ".organization-overview, .organization-computers, .organization-people, .organization-member-results, .organization-history-grid",
    );
    if (!sections?.length) return;
    const layout = Array.from(sections, (section) => ({
      section,
      size: section.getBoundingClientRect().height,
      blockSize: section.style.blockSize,
      overflow: section.style.overflow,
    }));
    const pointerId = event.pointerId;
    const controller = new AbortController();
    gesture.current = controller;
    const options = { capture: true, signal: controller.signal };
    let released = false;
    let expiry: number | undefined;
    const abort = () => controller.abort();

    // A response can move the control before the browser dispatches its click.
    for (const { section, size } of layout) {
      section.style.blockSize = `${size}px`;
      if (!openSelect || !section.contains(openSelect)) section.style.overflow = "clip";
    }
    window.addEventListener("pointerup", (up) => {
      if (up.pointerId !== pointerId) return;
      released = true;
      // Touch browsers dispatch click after pointerup. A canceled gesture may send no click.
      expiry = window.setTimeout(abort, up.pointerType === "touch" ? 400 : 100);
    }, options);
    window.addEventListener("click", (click) => {
      if (!released || click.detail === 0) return;
      window.clearTimeout(expiry);
      expiry = window.setTimeout(abort);
    }, options);
    window.addEventListener("pointercancel", (cancel) => {
      if (cancel.pointerId === pointerId) abort();
    }, options);
    window.addEventListener("pointerdown", abort, options);
    window.addEventListener("blur", abort, { signal: controller.signal });
    controller.signal.addEventListener("abort", () => {
      window.clearTimeout(expiry);
      for (const { section, blockSize, overflow } of layout) {
        section.style.blockSize = blockSize;
        section.style.overflow = overflow;
      }
    }, { once: true });
  };
}

function OrganizationLookupStatus({ error, retry, restoreFocus }: {
  error: unknown;
  retry: () => void;
  restoreFocus: () => void;
}) {
  const { t } = useI18n();
  const status = useRef<HTMLDivElement>(null);
  useRestoreFocusOnRemoval(status, restoreFocus);
  return <div ref={status} role="group" aria-label={t("organizationLookup")} tabIndex={error ? -1 : 0}>
    {error ? <ErrorBox error={error} retry={retry} /> : <Loading />}
  </div>;
}

function InvitationActions({ summaryId, disabled, onAction }: {
  summaryId: string;
  disabled: boolean;
  onAction: (action: InvitationAction, trigger: HTMLButtonElement) => void;
}) {
  const { t } = useI18n();
  const actions = useRef<HTMLDivElement>(null);
  const restoreFocus = useCallback(() => {
    if (document.activeElement === document.body) document.getElementById(summaryId)?.focus({ preventScroll: true });
  }, [summaryId]);
  useRestoreFocusOnRemoval(actions, restoreFocus);

  return <>
    <p className="field-help">{t("renewLinkHelp")}</p>
    <div ref={actions} className="member-actions">
      <button type="button" className="button secondary compact" aria-disabled={disabled || undefined}
        onClick={(event) => onAction("link", event.currentTarget)}>{t("renewInvitationLink")}</button>
      <button type="button" className="text-button danger-text" aria-disabled={disabled || undefined}
        onClick={(event) => onAction("revoke", event.currentTarget)}>{t("revokeInvitation")}</button>
    </div>
  </>;
}

export function Organization({ memberSearchDrafts }: { memberSearchDrafts: Map<string, string> }) {
  const location = useLocation();
  const navigate = useNavigate();
  const userId = useSession().user!.id;
  const directorySearch =
    typeof location.state?.directorySearch === "string"
      ? location.state.directorySearch
      : "";
  const { companyId = "" } = useParams();
  const showMembers = !!useMatch("/organizations/:companyId/members");
  const [params] = useSearchParams();
  const memberSearch = useMemberSearch(memberSearchDrafts);
  const directory = `/organizations${directorySearch}`;
  const organizationPath = `/organizations/${companyId}`;
  const previousView = useRef({ companyId, showMembers });
  const key = `${userId}/${companyId}`;
  const view = useMemo(() => Symbol(key), [key]);
  const backLink = useRef<HTMLAnchorElement>(null);
  const restoreRouteFocus = useCallback(() => {
    if (document.activeElement === document.body) backLink.current?.focus({ preventScroll: true });
  }, []);
  const [returnView, setReturnView] = useState<symbol | null>(null);
  const [check, setCheck] = useState<CompanyCheck | null>(null);
  const [memberRevision, setMemberRevision] = useState(0);
  const [invitationRevision, setInvitationRevision] = useState(0);
  const currentCheck = check?.view === view ? check : null;
  const state = useData(
    async (signal) => {
      const company = await withDeadline(signal, (lookupSignal) =>
        api<Company>(`companies/${companyId}`, { signal: lookupSignal }),
      ).catch((error) => {
        if (currentCheck && error instanceof ApiError && error.status === 404) {
          return null;
        }
        throw error;
      });
      return { key, company, check: currentCheck };
    },
    key,
    true,
  );
  const lookup = state.data?.key === key ? state.data : null;
  const company = lookup?.company?.id === companyId ? lookup.company : null;
  const membershipId = company?.my_membership_id;
  const reload = state.reload;
  const checking = currentCheck !== null && lookup?.check !== currentCheck;
  const recheck = useCallback((reason: CompanyCheck["reason"]) => {
    setCheck((pending) => ({
      view,
      reason: pending?.view === view && pending.reason === "access" ? "access" : reason,
    }));
    reload();
  }, [view, reload]);
  const recheckMemberAccess = useCallback(() => recheck("update"), [recheck]);
  const finishMemberReturn = useCallback(() => setReturnView(null), []);
  useLayoutEffect(() => {
    if (previousView.current.companyId === companyId && previousView.current.showMembers !== showMembers) {
      setReturnView(showMembers ? null : view);
      if (company) recheck("access");
    }
    previousView.current = { companyId, showMembers };
  }, [companyId, showMembers, view, company, recheck]);
  useLayoutEffect(() => {
    if (currentCheck && !checking) {
      setCheck((pending) => pending === currentCheck ? null : pending);
    }
  }, [currentCheck, checking]);

  useLayoutEffect(() => {
    return subscribeToOrganizationChanges((change) => {
      if (change.type === "created" || change.userId !== userId || change.companyId !== companyId) return;
      if (change.type === "removed") {
        if (!change.membershipId || (change.source === view && change.membershipId === membershipId)) {
          navigate(directory, { replace: true });
        } else if (!membershipId || change.membershipId === membershipId) {
          recheck("access");
        }
        return;
      }
      if (change.type === "membersChanged") {
        setMemberRevision((value) => value + 1);
        setInvitationRevision((value) => value + 1);
        reload();
        return;
      }
      if (change.type === "invitationsChanged") {
        setInvitationRevision((value) => value + 1);
        return;
      }
      // A reopened page may already have a newer save than this response.
      if (change.source !== view) {
        recheck("update");
        return;
      }
      state.update((current) => {
        if (current?.key !== key || !current.company) return current;
        if (change.type === "renamed") {
          return { ...current, company: { ...current.company, name: change.name } };
        }
        if (change.type === "appearanceUpdated") {
          return { ...current, company: { ...current.company, ...change.appearance } };
        }
        if (current.company.my_membership_id !== change.member.id) return current;
        return {
          ...current,
          company: {
            ...current.company,
            my_role: change.member.role,
            my_permissions: change.member.effective_permissions,
          },
        };
      });
      reload();
    });
  }, [userId, companyId, membershipId, view, key, directory, navigate, reload, recheck, state.update]);
  const { t } = useI18n();
  if (lookup?.company === null) return <Navigate replace to={directory} />;

  const legacyPage = organizationPage(params);
  if (!showMembers && legacyPage > 1) {
    const next = new URLSearchParams(params);
    const page = Math.floor((legacyPage - 1) * 10 / memberDirectoryPageSize) + 1;
    if (page > 1) next.set("page", String(page));
    else next.delete("page");
    return <Navigate replace to={{ pathname: `${organizationPath}/members`, search: next.toString() }} state={location.state} />;
  }
  const memberQuery = new URLSearchParams();
  const q = typeof location.state?.memberSearch === "string" ? location.state.memberSearch : memberSearch.input;
  if (q) memberQuery.set("q", q);
  const onMembersChanged = () => notifyOrganizationChange({ type: "membersChanged", userId, companyId, source: view });
  const onInvitationsChanged = () => notifyOrganizationChange({ type: "invitationsChanged", userId, companyId, source: view });
  const onMembershipChanged = (member: Membership) => notifyOrganizationChange({
    type: "membershipUpdated", userId, companyId, member, source: view,
  });
  const loadingCompany = (!company || checking && currentCheck.reason === "access") && (state.loading || checking);
  return (
    <>
      <Link ref={backLink} key={showMembers ? "organization" : "directory"} className="back-link"
        to={showMembers ? { pathname: organizationPath, search: memberQuery.toString() } : directory}
        state={showMembers ? { directorySearch } : undefined}>
        ← {t(showMembers ? "backOrganization" : "backOrganizations")}
      </Link>
      {(!!state.error || loadingCompany) && <OrganizationLookupStatus key={state.error ? "error" : "pending"}
        error={state.error} restoreFocus={restoreRouteFocus}
        retry={() => {
          backLink.current?.focus({ preventScroll: true });
          reload();
        }} />}
      {company && !(checking && currentCheck.reason === "access") ? (
        showMembers ? <OrganizationMembers
          key={`${companyId}/${membershipId}`}
          company={company}
          memberSearch={memberSearch}
          directory
          revision={memberRevision}
          check={checking ? { error: state.error, retry: reload } : undefined}
          onChanged={onMembersChanged}
          onAccessError={recheckMemberAccess}
          onMembershipChanged={onMembershipChanged}
        /> : <OrganizationContent
          key={`${companyId}/${membershipId}`}
          company={company}
          directorySearch={directorySearch}
          memberSearch={memberSearch}
          onMemberReturn={returnView === view ? finishMemberReturn : undefined}
          memberRevision={memberRevision}
          invitationRevision={invitationRevision}
          onMembersChanged={onMembersChanged}
          onInvitationsChanged={onInvitationsChanged}
          onMemberAccessError={recheckMemberAccess}
          checkingCompany={checking}
          companyCheckError={checking ? state.error : null}
          onChanged={reload}
          onCheckPermissions={() => recheck("update")}
          onRenamed={(name) => notifyOrganizationChange({
            type: "renamed", userId, companyId, name, source: view,
          })}
          onAppearanceChanged={(appearance) => notifyOrganizationChange({
            type: "appearanceUpdated", userId, companyId, appearance, source: view,
          })}
          onMembershipChanged={onMembershipChanged}
          onAccessRemoved={(removedMembershipId) => notifyOrganizationChange({
            type: "removed",
            userId,
            companyId,
            membershipId: removedMembershipId,
            source: view,
          })}
        />
      ) : null}
    </>
  );
}

function OrganizationHero({ company, onSettings }: { company: Company; onSettings: () => void }) {
  const { t } = useI18n();
  const background = organizationImagePath(company, "background");
  const avatar = organizationImagePath(company, "avatar");
  const breakAt = company.name.indexOf(" ");
  return (
    <header className="organization-hero">
      <div className={`organization-hero-art${background ? " is-custom" : ""}`} aria-hidden="true">
        <ApiImage path={background} fallback={<img src={organizationArtwork} alt="" />} />
      </div>
      <div className="organization-hero-copy">
        <div className="organization-hero-context">
          {avatar && <span className="organization-hero-avatar" aria-hidden="true"><ApiImage path={avatar} fallback={null} /></span>}
          <p className="eyebrow">{t("organizationType")}</p>
        </div>
        <h1 aria-label={`${company.name}.`}>
          {breakAt > 0 ? <>{company.name.slice(0, breakAt)}{" "}<br /><span>{company.name.slice(breakAt + 1)}</span></> : company.name}
          <span className="hot" aria-hidden="true">.</span>
        </h1>
        <p className="lede">{t("sharedWorkspace")}</p>
        <button type="button" className="button primary compact organization-settings-trigger" aria-haspopup="dialog" onClick={(event) => {
          event.currentTarget.focus({ preventScroll: true });
          onSettings();
        }}><Icon name="building" size={16} />{t("organizationSettings")}</button>
      </div>
    </header>
  );
}

function OrganizationContent({
  company,
  directorySearch,
  memberSearch,
  onMemberReturn,
  memberRevision,
  invitationRevision,
  checkingCompany,
  companyCheckError,
  onChanged,
  onMembersChanged,
  onInvitationsChanged,
  onMemberAccessError,
  onCheckPermissions,
  onRenamed,
  onAppearanceChanged,
  onMembershipChanged,
  onAccessRemoved,
}: {
  company: Company;
  directorySearch: string;
  memberSearch: MemberSearch;
  onMemberReturn?: () => void;
  memberRevision: number;
  invitationRevision: number;
  checkingCompany: boolean;
  companyCheckError: unknown;
  onChanged: () => void;
  onMembersChanged: () => void;
  onInvitationsChanged: () => void;
  onMemberAccessError: () => void;
  onCheckPermissions: () => void;
  onRenamed: (name: string) => void;
  onAppearanceChanged: (appearance: CompanyAppearance) => void;
  onMembershipChanged: (member: Membership) => void;
  onAccessRemoved: (membershipId?: string) => void;
}) {
  const { t, locale } = useI18n();
  const { user } = useSession();
  const memberQuery = memberSearch.input;
  const canManage = company.my_permissions.includes("members.manage");
  const canDelete = company.my_role === "creator";
  const canReadResources = company.my_permissions.includes("resources.read");
  const resourceAccess: OrganizationResourceAccess = checkingCompany
    ? (companyCheckError ? "unverified" : "checking")
    : (canReadResources ? "available" : "denied");
  const pendingCheck = checkingCompany ? { error: companyCheckError, retry: onChanged } : undefined;
  const computersQuery = new URLSearchParams({
    company_id: company.id,
    only_available: "false",
    ...(directorySearch ? { directory_search: directorySearch } : {}),
    ...(memberQuery ? { organization_q: memberQuery } : {}),
  });
  const accessKey = `${company.id}/${company.my_role}/${company.my_permissions.join(",")}`;
  const [memberCount, setMemberCount] = useState<number | null>(null);
  const [people, setPeople] = useState<"members" | "invitations">("members");
  const [invitationsOpened, setInvitationsOpened] = useState(false);
  const invitationPath = `invitations?company_id=${company.id}`;
  const invitations = useData(
    async (signal) => {
      if (!canManage) return { key: accessKey, items: [], count: 0 };
      if (!invitationsOpened) {
        const { total } = await withDeadline(signal, (readSignal) =>
          request<Invitation[]>(`${invitationPath}&limit=0`, { signal: readSignal }),
        );
        return { key: accessKey, items: null, count: total };
      }
      const items = await allPages<Invitation>(`${invitationPath}&include_counts=false`, signal, {
        deadlineMs: DEFAULT_TIMEOUT_MS,
      });
      return { key: accessKey, items, count: items.length };
    },
    accessKey,
    true,
    invitationRevision,
  );
  const invitationData = invitations.data?.key === accessKey ? invitations.data : null;
  const invitationRows = invitationData?.items ?? null;
  const [invitationAccess, setInvitationAccess] = useState(() => ({
    key: accessKey, role: company.my_role, permissions: company.my_permissions, revision: 0,
  }));
  if (invitationAccess.key !== accessKey) {
    // A loss of membership privileges revokes the issuer's pending invitations.
    const role = company.my_role;
    const lostRole = invitationAccess.role === "creator" && role !== "creator" ||
      invitationAccess.role === "admin" && role === "user";
    const lostPermissions = invitationAccess.permissions.some((permission) => !company.my_permissions.includes(permission));
    setInvitationAccess({ key: accessKey, role, permissions: company.my_permissions,
      revision: invitationAccess.revision + Number(lostRole || lostPermissions) });
  }
  const accessRevision = invitationAccess.revision;
  const [leaving, setLeaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [renewed, setRenewed] = useState<{ invitation: CreatedInvitation; accessRevision: number } | null>(null);
  if (renewed && (renewed.accessRevision !== accessRevision || invitationRows?.some((invitation) =>
    invitation.id === renewed.invitation.id && invitation.status !== "pending"))) setRenewed(null);
  const token = renewed?.invitation.token;
  const renewedLink = useRef<HTMLInputElement>(null);
  const focusRenewedLink = useRef(false);
  const [linkRevision, setLinkRevision] = useState(0);
  const [inviteBusy, setInviteBusy] = useState<string | null>(null);
  const inviteActionsDisabled = !!inviteBusy || invitations.loading;
  const [inviteError, setInviteError] = useState<unknown>(null);
  const [settings, setSettings] = useState(false);
  const [expandedTeamRows, setExpandedTeamRows] = useState(() => new Set<string>());
  const holdTeamLayout = useTeamPointerLayout();
  const peopleId = useId();
  const canReadMembers = company.my_permissions.includes("members.read");
  const teamCount = (canReadMembers ? memberCount : null) ?? company.member_count;
  const peopleOptions: { key: typeof people; label: string; count?: string }[] = [{
    key: "members", label: t("members"),
    count: canReadMembers ? teamCount.toLocaleString(locale) : undefined,
  }];
  if (canManage) peopleOptions.push({
    key: "invitations", label: t("invitations"), count: invitationData?.count?.toLocaleString(locale),
  });
  const activePeople = canManage ? people : "members";
  function changePeople(section: typeof people) {
    setPeople(section);
    if (section === "invitations" && !invitationsOpened) {
      setInvitationsOpened(true);
      invitations.reload();
    }
  }
  function toggleTeamRow(id: string) {
    setExpandedTeamRows((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }
  useEffect(() => {
    if (!canDelete) setDeleting(false);
  }, [canDelete]);
  useEffect(() => {
    if (!token || !focusRenewedLink.current) return;
    focusRenewedLink.current = false;
    if (activePeople !== "invitations" || settings) return;
    const input = renewedLink.current;
    if (!input) return;
    input.focus({ preventScroll: true });
    const frame = requestAnimationFrame(() => {
      if (document.activeElement === input) input.scrollIntoView({ block: "center" });
    });
    return () => cancelAnimationFrame(frame);
  }, [token, activePeople, settings]);
  async function inviteAction(
    invitation: Invitation,
    action: InvitationAction,
    trigger: HTMLButtonElement,
  ) {
    if (inviteActionsDisabled) return;
    trigger.focus({ preventScroll: true });
    setInviteBusy(invitation.id);
    setInviteError(null);
    try {
      if (action === "link") {
        const result = await api<CreatedInvitation>(
          `invitations/${invitation.id}/link`,
          json("POST"),
        );
        focusRenewedLink.current = document.activeElement === trigger;
        setRenewed({ invitation: result, accessRevision });
      } else {
        await api(`invitations/${invitation.id}`, json("DELETE"));
        setRenewed(null);
      }
      onInvitationsChanged();
      setLinkRevision((value) => value + 1);
    } catch (e) {
      setInviteError(e);
      if (e instanceof ApiError && (e.status === 403 || e.status === 404)) onMemberAccessError();
    } finally {
      setInviteBusy(null);
    }
  }
  const statuses: Record<Invitation["status"], MessageKey> = {
    pending: "invitationPending",
    accepted: "invitationStatusAccepted",
    declined: "invitationStatusDeclined",
    revoked: "invitationStatusRevoked",
    expired: "invitationStatusExpired",
  };
  const memberPanel = <OrganizationMembers company={company} memberSearch={memberSearch} revision={memberRevision} check={pendingCheck}
    onReturn={onMemberReturn} knownCount={memberCount} onCount={setMemberCount} onMembershipChanged={onMembershipChanged}
    onAccessError={onMemberAccessError} onChanged={onMembersChanged} />;
  const invitationPanel = canManage && (
        <section
          className="outgoing-invitations"
          aria-label={t("invitations")}
        >
          <MemberSharing company={company} onChanged={(addedMember) => {
            if (addedMember) onMembersChanged();
            else onInvitationsChanged();
          }} onAccessError={onMemberAccessError} invitations={invitationRows}
            accessRevision={accessRevision} linkRevision={linkRevision} />
          <ErrorBox error={inviteError} conflict="invitationConflict" />
          {token && <ShareLink key={token} token={token} inputRef={renewedLink} />}
          {invitations.error ? (
            <ErrorBox error={invitations.error} retry={invitations.reload} />
          ) : !invitationRows ? (
            <Loading />
          ) : !invitationRows.length ? (
            <p className="organization-invitations-empty">{t("noInvitations")}</p>
          ) : (
            <div className="organization-team-list" aria-busy={invitations.loading || undefined}>
              {invitationRows.map((invitation) => {
                const label = invitation.email || t("openInvitationLabel");
                const detailsId = `${peopleId}-invitation-${invitation.id}`;
                const expanded = expandedTeamRows.has(detailsId);
                return (
                  <article key={invitation.id} className="invitation-row" aria-label={label}>
                    <h3 className="organization-team-row-heading">
                      <button id={`${detailsId}-summary`} className="organization-invitation-summary" type="button" aria-expanded={expanded}
                        aria-controls={detailsId} onClick={() => toggleTeamRow(detailsId)}>
                        <span className="organization-invitation-label">
                          <strong>{label}</strong>
                          {!invitation.email && <small>
                            {t(invitation.role)} · <InvitationExpiry expiresAt={invitation.expires_at} />
                          </small>}
                        </span>
                        <span className={`organization-invitation-status ${invitation.status}`}>{t(statuses[invitation.status])}</span>
                        <span className="organization-team-chevron"><Icon name="chevron-down" size={15} /></span>
                      </button>
                    </h3>
                    <div className="organization-invitation-details" id={detailsId} hidden={!expanded}>
                      {invitation.email
                        ? <InvitationFacts invitation={invitation} />
                        : <PermissionSummary values={invitation.effective_permissions} />}
                      {invitation.status === "pending" && (company.my_role === "creator" || invitation.role === "user") && (
                        <InvitationActions summaryId={`${detailsId}-summary`} disabled={inviteActionsDisabled}
                          onAction={(action, trigger) => inviteAction(invitation, action, trigger)} />
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </section>
  );
  return (
    <div className="organization-detail">
      <OrganizationHero company={company} onSettings={() => setSettings(true)} />
      <OrganizationOverview company={company} memberCount={teamCount} access={resourceAccess} />
      <WidgetLayout className="organization-widgets" gap="var(--organization-gap)" primaryWeight={1.32}
        lead={<OrganizationComputers company={company} query={computersQuery.toString()} access={resourceAccess} />}
        aside={<section className="organization-widget organization-people" aria-label={t("organizationTeam")}
            onFocus={keepOrganizationControlVisible} onPointerDownCapture={holdTeamLayout}>
            <header className="organization-team-header">
              <h2><span className="organization-team-icon"><Icon name="users" size={18} /></span>{t("organizationTeam")}</h2>
            </header>
            {canManage && <SectionTabs id={peopleId} label={t("organizationTeam")} options={peopleOptions} active={activePeople} onChange={changePeople} />}
            {canManage ? <div id={`${peopleId}-panel-members`} role="tabpanel" aria-labelledby={`${peopleId}-tab-members`} hidden={activePeople !== "members"}>
              {memberPanel}
            </div> : memberPanel}
            {canManage && <div id={`${peopleId}-panel-invitations`} role="tabpanel" aria-labelledby={`${peopleId}-tab-invitations`} hidden={activePeople !== "invitations"}>
              {invitationPanel}
            </div>}
          </section>}>
        {canReadResources && <Activity mode={resourceAccess === "available" ? "visible" : "hidden"}>
          <OrganizationResourceCharts companyId={company.id} />
        </Activity>}
      </WidgetLayout>
      {settings && <OrganizationSettings company={company}
        check={pendingCheck}
        onClose={() => setSettings(false)} onLeave={() => setLeaving(true)} onDelete={() => setDeleting(true)}
        onRenamed={onRenamed} onAppearanceChanged={onAppearanceChanged} onCheckPermissions={onCheckPermissions} />}
      {leaving && (
        <ConfirmDialog
          check={pendingCheck}
          title={t("confirmLeave")}
          value={memberName(user!)}
          warning={t("leaveWarning")}
          conflict="leaveBlocked"
          onClose={() => setLeaving(false)}
          onConfirm={async () => {
            await api(`memberships/${company.my_membership_id}`, json("DELETE"));
            onAccessRemoved(company.my_membership_id);
          }}
        />
      )}
      {deleting && canDelete && (
        <ConfirmDialog
          check={pendingCheck}
          title={t("deleteOrganization")}
          warning={t("irreversible")}
          value={company.name}
          conflict="blockedDelete"
          onClose={() => setDeleting(false)}
          onConfirm={async () => {
            await api(`companies/${company.id}`, json("DELETE"));
            onAccessRemoved();
          }}
        />
      )}
    </div>
  );
}
