import { Activity, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type RefObject } from "react";
import { WidgetLayout } from "@mutating/ui/react";
import "@mutating/ui/styles/widget-layout.css";
import {
  Link,
  Navigate,
  useLocation,
  useMatch,
  useNavigate,
  useNavigationType,
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
  type ApiResponse,
} from "../lib/api";
import { useRefreshWhileVisible } from "../lib/useData";
import { OutdatedPoolRead, usePoolRead, useReloadOnComputerPoolChange } from "../lib/compute";
import { useSession } from "../lib/session";
import {
  notifyOrganizationChange,
  subscribeToOrganizationChanges,
  useOrganizationMembershipCheck,
  useOrganizationSectionAccess,
  type OrganizationMemberPermission,
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
  InvitationLinkCheck,
  Membership,
  OrganizationMembershipCheck,
} from "../lib/types";
import { ConfirmDialog, ErrorBox, Icon, Loading, RetryErrorBox, useRestoreFocusOnRemoval } from "../components/UI";
import { MemberSharing } from "../components/MemberSharing";
import { keepOrganizationControlVisible, memberDirectoryPageSize, OrganizationMembers, useMemberSearch, type MemberSearch } from "../components/OrganizationMembers";
import { OrganizationHero } from "../components/OrganizationHero";
import { SectionTabs } from "../components/SectionTabs";
import { OrganizationSettings } from "../components/OrganizationSettings";
import { OrganizationResourceCharts, PoolComputers, PoolOverview, usePoolOverview, usePoolPointerLayout,
  type OrganizationResourceAccess } from "../components/OrganizationResources";
import {
  InvitationExpiry,
  InvitationFacts,
  PermissionSummary,
  ShareLink,
  invitationLinkIsOutdated,
} from "../components/InvitationUI";
import "../styles/organization.css";

const checkPriority = { section: 0, metadata: 1, update: 2, access: 3 } as const;
type CompanyCheck = { view: symbol; reason: keyof typeof checkPriority };
type InvitationRefresh = { revision: number; verifyFrom: number; checks: ReadonlyMap<string, InvitationLinkCheck> };
type InvitationAction = "link" | "revoke";
const resourceWidgets = ".organization-overview, .organization-computers, .organization-history-grid";

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
  const navigationType = useNavigationType();
  const user = useSession().user!;
  const userId = user.id;
  const directorySearch =
    typeof location.state?.directorySearch === "string"
      ? location.state.directorySearch
      : "";
  const connectedPoolOrigin = location.state?.connectedPoolOrigin;
  const poolNavigationState = connectedPoolOrigin ? { connectedPoolOrigin } : undefined;
  const companyId = useParams().companyId?.toLowerCase() ?? "";
  const showMembers = !!useMatch("/organizations/:companyId/members");
  const [params] = useSearchParams();
  const memberSearch = useMemberSearch(memberSearchDrafts);
  const directory = `/organizations${directorySearch}`;
  const organizationPath = `/organizations/${companyId}`;
  const previousView = useRef({ companyId, showMembers });
  const key = `${userId}/${companyId}`;
  const view = useMemo(() => Symbol(key), [key]);
  const { checked: membershipCheck, remember: recordMembership, clearPermission, resourcesRead,
    precedesMembershipCheck, rememberMemberPermission, newerMemberPermission } = useOrganizationMembershipCheck(key);
  const acceptCompanyError = useCallback((error: ApiError) => {
    if (error.status === 404) return !precedesMembershipCheck(error.poolCheckRevision);
    return error.status !== 403 || !error.organizationMembershipId ||
      !precedesMembershipCheck(error.poolCheckRevision, error.organizationMembershipId);
  }, [precedesMembershipCheck]);
  const ensureCurrentMembership = useCallback((id: string, revision?: number) => {
    if (revision !== undefined && precedesMembershipCheck(revision, id)) throw new OutdatedPoolRead(revision);
  }, [precedesMembershipCheck]);
  const [resourceDenial, setResourceDenial] = useState<symbol | null>(null);
  const verifyResourcePermission = useCallback((proof: { id: string; revision?: number; resourcesRead: boolean }) => {
    if (!recordMembership(proof)) return false;
    setResourceDenial((current) => current === view ? null : current);
    return true;
  }, [recordMembership, view]);
  const recordReadMembership = (member: OrganizationMembershipCheck, permission: "resources.read" | OrganizationMemberPermission) => {
    ensureCurrentMembership(member.id, member.revision);
    if (permission === "resources.read") verifyResourcePermission({ ...member, resourcesRead: true });
    else {
      recordMembership(member);
      rememberMemberPermission(permission, member.id, true, member.revision);
    }
  };
  const [leaving, setLeaving] = useState<{ view: symbol; membershipId: string } | null>(null);
  const previousLocation = useRef(location);
  useLayoutEffect(() => {
    const previous = previousLocation.current;
    previousLocation.current = location;
    const searchChangedInPlace = navigationType === "REPLACE" && location.state?.keepScroll &&
      previous.pathname === location.pathname && previous.hash === location.hash && previous.search !== location.search;
    if (previous.key !== location.key && !searchChangedInPlace) setLeaving(null);
  }, [location, navigationType]);
  const backLink = useRef<HTMLAnchorElement>(null);
  const settingsButton = useRef<HTMLButtonElement>(null);
  const restoreRouteFocus = useCallback(() => {
    if (document.activeElement === document.body) backLink.current?.focus({ preventScroll: true });
  }, []);
  const restoreLeaveFocus = useCallback(() => {
    if (document.activeElement === document.body) {
      (settingsButton.current ?? backLink.current)?.focus({ preventScroll: true });
    }
  }, []);
  const focusBackWithin = useCallback((selector: string) => {
    if (document.activeElement?.closest(selector)) {
      backLink.current?.focus();
    }
  }, []);
  const [returnView, setReturnView] = useState<symbol | null>(null);
  const [check, setCheck] = useState<CompanyCheck | null>(null);
  const [memberRevision, setMemberRevision] = useState(0);
  const [invitationRefresh, setInvitationRefresh] = useState<InvitationRefresh>(() => ({
    revision: 0, verifyFrom: 0, checks: new Map(),
  }));
  const refreshInvitations = useCallback((verify = false, invitation?: InvitationLinkCheck) => {
    setInvitationRefresh(({ revision, verifyFrom, checks }) => {
      const previous = invitation ? checks.get(invitation.id) : undefined;
      if (invitation && (!previous || previous.status === "pending" &&
          (invitation.status === "revoked" || (invitation.link_check_revision ?? 0) > (previous.link_check_revision ?? 0)))) {
        checks = new Map(checks).set(invitation.id, invitation);
      }
      return { revision: revision + 1, verifyFrom: verify ? revision + 1 : verifyFrom, checks };
    });
  }, []);
  const currentCheck = check?.view === view ? check : null;
  const state = usePoolRead<{ key: string; company: Company | null; check: CompanyCheck | null; poolCheckRevision?: number }>(
    async (signal) => {
      try {
        const { data: company, poolCheckRevision } = await withDeadline(signal, (lookupSignal) =>
          request<Company>(`companies/${companyId}`, { signal: lookupSignal }),
        );
        if (!signal.aborted) {
          ensureCurrentMembership(company.my_membership_id, poolCheckRevision);
          const canRead = company.my_permissions.includes("resources.read");
          const accepted = verifyResourcePermission({
            id: company.my_membership_id, revision: poolCheckRevision,
            resourcesRead: canRead,
          });
          if (accepted && !canRead) focusBackWithin(resourceWidgets);
          let conflictingPermission = false;
          for (const permission of ["members.read", "members.manage"] as const) {
            const allowed = company.my_permissions.includes(permission);
            const newer = newerMemberPermission(permission, company.my_membership_id, poolCheckRevision);
            if (newer !== undefined) conflictingPermission ||= newer !== allowed;
            else rememberMemberPermission(permission, company.my_membership_id, allowed, poolCheckRevision);
          }
          if (conflictingPermission && poolCheckRevision !== undefined) throw new OutdatedPoolRead(poolCheckRevision);
        }
        return { key, company, check: currentCheck, poolCheckRevision };
      } catch (error) {
        if (!signal.aborted && error instanceof ApiError && [401, 403, 404].includes(error.status) && acceptCompanyError(error)) {
          focusBackWithin(".organization-detail");
          if (error.status === 404 && (currentCheck || state.data?.key === key)) {
            return { key, company: null, check: currentCheck };
          }
          state.update(null);
        }
        throw error;
      }
    },
    key,
    true,
    { retainError: true, onAccessError: acceptCompanyError },
  );
  const lookup = state.data?.key === key ? state.data : null;
  const company = lookup?.company?.id === companyId ? lookup.company : null;
  const membershipId = company?.my_membership_id;
  const reload = state.reload;
  useReloadOnComputerPoolChange(userId, ({ assignment }) => {
    if (assignment?.companyId === companyId && assignment.membershipId) {
      const accepted = recordMembership({
        id: assignment.membershipId, revision: assignment.revision, resourcesRead: assignment.canRead,
      });
      if (accepted && !assignment.canRead) focusBackWithin(resourceWidgets);
    }
    reload();
  }, companyId);
  useRefreshWhileVisible(reload);
  const checking = currentCheck !== null && lookup?.check !== currentCheck;
  const recheck = useCallback((reason: CompanyCheck["reason"]) => {
    setCheck((pending) => ({
      view,
      reason: pending?.view === view && checkPriority[pending.reason] > checkPriority[reason] ? pending.reason : reason,
    }));
    if (reason === "metadata" || reason === "update") {
      focusBackWithin(".outgoing-invitations .share-link");
      refreshInvitations(true);
    }
    reload();
  }, [view, reload, focusBackWithin, refreshInvitations]);
  const recheckContentAccess = useCallback((error?: ApiError, permission?: OrganizationMemberPermission) => {
    if (error && !acceptCompanyError(error)) return false;
    if (permission && error?.status === 403 && error.organizationMembershipId &&
        !rememberMemberPermission(permission, error.organizationMembershipId, false, error.poolCheckRevision)) return false;
    if (error?.status === 404) focusBackWithin(".organization-detail");
    recheck(error?.status === 404 ? "access" : error ? "section" : "update");
    return true;
  }, [acceptCompanyError, rememberMemberPermission, focusBackWithin, recheck]);
  const recheckResourceAccess = useCallback((error: ApiError) => {
    if (error.status === 403) {
      if (error.organizationId === companyId && error.organizationMembershipId && error.poolCheckRevision !== undefined) {
        const accepted = verifyResourcePermission({
          id: error.organizationMembershipId, revision: error.poolCheckRevision, resourcesRead: false,
        });
        if (!accepted) return false;
      } else {
        setResourceDenial(view);
      }
      focusBackWithin(resourceWidgets);
    }
    return recheckContentAccess(error);
  }, [companyId, view, verifyResourcePermission, focusBackWithin, recheckContentAccess]);
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
        const checked = membershipCheck.current;
        const verifiedId = checked?.key === key ? checked.id : membershipId;
        if (!change.membershipId || (change.source === view && change.membershipId === verifiedId)) {
          navigate(directory, { replace: true, state: connectedPoolOrigin ? { connectedPoolOrigin } : undefined });
        } else if (!verifiedId || change.membershipId === verifiedId) {
          recheck("access");
        } else if (change.membershipId === membershipId) {
          recheck("metadata");
        }
        return;
      }
      if (change.type === "membersChanged") {
        setMemberRevision((value) => value + 1);
        refreshInvitations();
        reload();
        return;
      }
      if (change.type === "invitationsChanged") {
        refreshInvitations(false, change.invitation);
        return;
      }
      // A reopened page may already have a newer save than this response.
      if (change.source !== view) {
        const metadataOnly = change.type === "renamed" || change.type === "appearanceUpdated";
        recheck(metadataOnly ? "metadata" : "update");
        return;
      }
      if (change.type === "membershipUpdated" && change.member.id === membershipId) clearPermission();
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
  }, [userId, companyId, membershipId, view, key, directory, navigate, reload, recheck, refreshInvitations, state.update, connectedPoolOrigin, clearPermission]);
  const { t } = useI18n();
  if (lookup?.company === null) return <Navigate replace to={directory} state={poolNavigationState} />;

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
  const onInvitationsChanged = (invitation?: InvitationLinkCheck) =>
    notifyOrganizationChange({ type: "invitationsChanged", userId, companyId, source: view, invitation });
  const onMembershipChanged = (member: Membership) => notifyOrganizationChange({
    type: "membershipUpdated", userId, companyId, member, source: view,
  });
  const onAccessRemoved = (removedMembershipId?: string) => notifyOrganizationChange({
    type: "removed", userId, companyId, membershipId: removedMembershipId, source: view,
  });
  const currentMembershipId = () => membershipCheck.current?.key === key ? membershipCheck.current.id : membershipId;
  const confirmedLeave = leaving?.view === view && leaving.membershipId === currentMembershipId() ? leaving : null;
  const loadingCompany = (!company || checking && currentCheck.reason === "access") && (state.loading || checking);
  return (
    <>
      <Link ref={backLink} key={showMembers ? "organization" : "directory"} className="back-link"
        to={showMembers ? { pathname: organizationPath, search: memberQuery.toString() } : directory}
        state={showMembers ? { directorySearch, connectedPoolOrigin } : poolNavigationState}>
        ← {t(showMembers ? "backOrganization" : "backOrganizations")}
      </Link>
      {company && !(checking && currentCheck.reason === "access") ? (
        showMembers ? <OrganizationMembers
          key={`${companyId}/${membershipId}`}
          company={company}
          companyRevision={lookup?.poolCheckRevision}
          memberSearch={memberSearch}
          directory
          revision={memberRevision}
          check={checking ? { error: state.error, retry: reload } : undefined}
          onChanged={onMembersChanged}
          onAccessError={(error) => recheckContentAccess(error, "members.read")}
          onMembershipCheck={(member) => recordReadMembership(member, "members.read")}
          onMembershipChanged={onMembershipChanged}
        /> : <OrganizationContent
          key={`${companyId}/${membershipId}`}
          company={company}
          companyRevision={lookup?.poolCheckRevision}
          directorySearch={directorySearch}
          memberSearch={memberSearch}
          onMemberReturn={returnView === view ? finishMemberReturn : undefined}
          memberRevision={memberRevision}
          invitationRefresh={invitationRefresh}
          onMembersChanged={onMembersChanged}
          onInvitationsChanged={onInvitationsChanged}
          onMemberAccessError={recheckContentAccess}
          onResourceAccessError={recheckResourceAccess}
          onMembershipCheck={recordReadMembership}
          onResourceRead={(member) => recordReadMembership(member, "resources.read")}
          verifiedResourceRead={resourcesRead}
          resourcesDenied={resourceDenial === view}
          settingsButton={settingsButton}
          onLeave={() => {
            const membershipId = currentMembershipId();
            if (membershipId) setLeaving({ view, membershipId });
          }}
          checkingCompany={checking}
          checkingResources={checking && (currentCheck.reason === "update" || currentCheck.reason === "access")}
          sectionOnlyCheck={currentCheck?.reason === "section"}
          companyCheckError={checking ? state.error : null}
          onChanged={reload}
          onRenamed={(name) => notifyOrganizationChange({
            type: "renamed", userId, companyId, name, source: view,
          })}
          onAppearanceChanged={(appearance) => notifyOrganizationChange({
            type: "appearanceUpdated", userId, companyId, appearance, source: view,
          })}
          onMembershipChanged={onMembershipChanged}
          onAccessRemoved={onAccessRemoved}
        />
      ) : null}
      {company && !showMembers && confirmedLeave && (
        <ConfirmDialog
          check={checking ? { error: state.error, retry: reload } : undefined}
          title={t("confirmLeave")}
          value={memberName(user)}
          warning={t("leaveWarning")}
          conflict="leaveBlocked"
          restoreFocus={restoreLeaveFocus}
          onClose={() => setLeaving(null)}
          onConfirm={async () => {
            await api(`memberships/${confirmedLeave.membershipId}`, json("DELETE"));
            onAccessRemoved(confirmedLeave.membershipId);
          }}
        />
      )}
      {(!!state.error || loadingCompany) && <OrganizationLookupStatus key={state.error ? "error" : "pending"}
        error={state.error} restoreFocus={restoreRouteFocus}
        retry={() => {
          backLink.current?.focus({ preventScroll: true });
          state.clearError();
          reload();
        }} />}
    </>
  );
}

function OrganizationContent({
  company,
  companyRevision,
  directorySearch,
  memberSearch,
  onMemberReturn,
  memberRevision,
  invitationRefresh,
  checkingCompany,
  checkingResources,
  sectionOnlyCheck,
  companyCheckError,
  onChanged,
  onMembersChanged,
  onInvitationsChanged,
  onMemberAccessError,
  onResourceAccessError,
  onMembershipCheck,
  onResourceRead,
  verifiedResourceRead,
  resourcesDenied,
  settingsButton,
  onLeave,
  onRenamed,
  onAppearanceChanged,
  onMembershipChanged,
  onAccessRemoved,
}: {
  company: Company;
  companyRevision?: number;
  directorySearch: string;
  memberSearch: MemberSearch;
  onMemberReturn?: () => void;
  memberRevision: number;
  invitationRefresh: InvitationRefresh;
  checkingCompany: boolean;
  checkingResources: boolean;
  sectionOnlyCheck: boolean;
  companyCheckError: unknown;
  onChanged: () => void;
  onMembersChanged: () => void;
  onInvitationsChanged: (invitation?: InvitationLinkCheck) => void;
  onMemberAccessError: (error?: ApiError, permission?: OrganizationMemberPermission) => boolean;
  onResourceAccessError: (error: ApiError) => boolean;
  onMembershipCheck: (membership: OrganizationMembershipCheck, permission: OrganizationMemberPermission) => void;
  onResourceRead: (membership: OrganizationMembershipCheck) => void;
  verifiedResourceRead?: boolean;
  resourcesDenied: boolean;
  settingsButton: RefObject<HTMLButtonElement | null>;
  onLeave: () => void;
  onRenamed: (name: string) => void;
  onAppearanceChanged: (appearance: CompanyAppearance) => void;
  onMembershipChanged: (member: Membership) => void;
  onAccessRemoved: (membershipId?: string) => void;
}) {
  const { t, locale } = useI18n();
  const memberQuery = memberSearch.input;
  const canManage = company.my_permissions.includes("members.manage");
  const canDelete = company.my_role === "creator";
  const metadataCanRead = company.my_permissions.includes("resources.read");
  const canReadResources = verifiedResourceRead ?? metadataCanRead;
  const resourceAccess: OrganizationResourceAccess = checkingResources
    ? (companyCheckError ? "unverified" : "checking")
    : (canReadResources && !resourcesDenied ? "available" : "denied");
  const resources = usePoolOverview({ kind: "companies", id: company.id }, {
    enabled: (canReadResources || metadataCanRead) && !checkingResources,
    onAccessError: onResourceAccessError,
    onRead: onResourceRead,
  });
  const { update: updateOverview, reload: reloadOverview } = resources;
  const checkResourceAccess = useCallback((error: ApiError) => {
    if (!onResourceAccessError(error)) return false;
    if (error.status === 403) {
      updateOverview(null);
      reloadOverview();
    }
    return true;
  }, [onResourceAccessError, updateOverview, reloadOverview]);
  const pendingCheck = checkingCompany ? { error: companyCheckError, retry: onChanged, scoped: sectionOnlyCheck } : undefined;
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
  const [lastInvitationDenial, setInvitationDenial] = useState<ApiError | null>(null);
  const invitationPath = `invitations?company_id=${company.id}`;
  const invitations = usePoolRead<{ key: string; items: Invitation[] | null; count: number | null; revision: number }>(
    async (signal) => {
      const revision = invitationRefresh.revision;
      if (!canManage) return { key: accessKey, items: [], count: 0, revision };
      let firstPage: OrganizationMembershipCheck | undefined;
      const recordPage = ({ organizationMembershipId: id, poolCheckRevision }: ApiResponse<Invitation[]>) => {
        if (signal.aborted || !id || poolCheckRevision === undefined) return;
        onMembershipCheck({ id, revision: poolCheckRevision }, "members.manage");
        if (firstPage && firstPage.id !== id) throw new OutdatedPoolRead(firstPage.revision);
        firstPage ??= { id, revision: poolCheckRevision };
      };
      if (!invitationsOpened) {
        const result = await withDeadline(signal, (readSignal) =>
          request<Invitation[]>(`${invitationPath}&limit=0`, { signal: readSignal }),
        );
        recordPage(result);
        if (!signal.aborted) setInvitationDenial(null);
        return { key: accessKey, items: null, count: result.total, revision };
      }
      const items = await allPages<Invitation>(`${invitationPath}&include_counts=false`, signal, {
        deadlineMs: DEFAULT_TIMEOUT_MS, onPage: recordPage,
      });
      if (!signal.aborted) setInvitationDenial(null);
      return { key: accessKey, items, count: items.length, revision };
    },
    accessKey,
    true,
    {
      revision: invitationRefresh.revision, retainError: true,
      onAccessError: (error) => {
        if (!onMemberAccessError(error, "members.manage")) return false;
        invitations.update(null);
        setInvitationDenial(error);
        return true;
      },
    },
  );
  const { denial: invitationDenial, error: invitationError } = useOrganizationSectionAccess(
    invitations, lastInvitationDenial, canManage ? companyRevision : undefined,
  );
  useRefreshWhileVisible(invitations.reload, canManage);
  const invitationData = !invitationDenial && invitations.data?.key === accessKey ? invitations.data : null;
  const invitationRows = invitationData?.items ?? null;
  const showInvitationLinks = invitationRefresh.verifyFrom === 0 ||
    Boolean(invitationData?.items && invitationData.revision >= invitationRefresh.verifyFrom);
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
  const [deleting, setDeleting] = useState(false);
  const [renewed, setRenewed] = useState<{ invitation: CreatedInvitation; accessRevision: number; verifiedFrom: number } | null>(null);
  if (renewed && (invitationDenial || renewed.accessRevision !== accessRevision ||
      invitationLinkIsOutdated(renewed.invitation, invitationRows, invitationRefresh.checks.get(renewed.invitation.id)))) setRenewed(null);
  const token = renewed && (showInvitationLinks || renewed.verifiedFrom === invitationRefresh.verifyFrom)
    ? renewed.invitation.token : undefined;
  const renewedLink = useRef<HTMLInputElement>(null);
  const focusRenewedLink = useRef(false);
  const [linkRevision, setLinkRevision] = useState(0);
  const [inviteBusy, setInviteBusy] = useState<string | null>(null);
  const inviteActionsDisabled = !!inviteBusy || invitations.loading;
  const [inviteError, setInviteError] = useState<unknown>(null);
  const [settings, setSettings] = useState(false);
  const [expandedTeamRows, setExpandedTeamRows] = useState(() => new Set<string>());
  const holdTeamLayout = usePoolPointerLayout();
  const peopleId = useId();
  const restoreInvitationFocus = useCallback(() => {
    if (document.activeElement === document.body) document.getElementById(`${peopleId}-tab-invitations`)?.focus();
  }, [peopleId]);
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
      let checked: InvitationLinkCheck;
      if (action === "link") {
        const result = await api<CreatedInvitation>(
          `invitations/${invitation.id}/link`,
          json("POST"),
        );
        focusRenewedLink.current = document.activeElement === trigger;
        setRenewed({ invitation: result, accessRevision, verifiedFrom: invitationRefresh.verifyFrom });
        const { id, status, link_version, link_check_revision } = result;
        checked = { id, status, link_version, link_check_revision };
      } else {
        await api(`invitations/${invitation.id}`, json("DELETE"));
        setRenewed(null);
        checked = { id: invitation.id, status: "revoked" };
      }
      onInvitationsChanged(checked);
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
  const memberPanel = <OrganizationMembers company={company} companyRevision={companyRevision}
    memberSearch={memberSearch} revision={memberRevision} check={pendingCheck}
    onReturn={onMemberReturn} knownCount={memberCount} onCount={setMemberCount} onMembershipChanged={onMembershipChanged}
    onAccessError={(error) => onMemberAccessError(error, "members.read")}
    onMembershipCheck={(member) => onMembershipCheck(member, "members.read")} onChanged={onMembersChanged} />;
  const invitationPanel = canManage && (
        <section
          className="outgoing-invitations"
          aria-label={t("invitations")}
        >
          {invitationDenial ? <RetryErrorBox error={invitationDenial} retry={invitations.reload} restoreFocus={restoreInvitationFocus} /> : <>
            <MemberSharing company={company} onChanged={(addedMember) => {
              if (addedMember) onMembersChanged();
              else onInvitationsChanged();
            }} onAccessError={onMemberAccessError} invitations={invitationRows}
              accessRevision={accessRevision} linkRevision={linkRevision} showInvitationLinks={showInvitationLinks}
              verifyInvitationsFrom={invitationRefresh.verifyFrom} invitationChecks={invitationRefresh.checks}
              restoreInvitationFocus={restoreInvitationFocus} />
            <ErrorBox error={inviteError} conflict="invitationConflict" />
            {token && <ShareLink key={token} token={token} inputRef={renewedLink} restoreFocus={restoreInvitationFocus} />}
            {invitationError ? (
              <RetryErrorBox error={invitationError} retry={invitations.reload} restoreFocus={restoreInvitationFocus} />
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
          </>}
        </section>
  );
  return (
    <div className="organization-detail">
      <OrganizationHero
        name={company.name}
        context={t("organizationType")}
        description={t("sharedWorkspace")}
        avatar={organizationImagePath(company, "avatar")}
        background={organizationImagePath(company, "background")}
        action={<button ref={settingsButton} type="button" className="button primary compact organization-hero-action organization-settings-trigger" aria-haspopup="dialog" onClick={(event) => {
          event.currentTarget.focus({ preventScroll: true });
          setSettings(true);
        }}><Icon name="building" size={16} />{t("organizationSettings")}</button>}
      />
      <PoolOverview state={resources} computerCount={company.computer_count} memberCount={teamCount} access={resourceAccess} />
      <WidgetLayout className="organization-widgets" gap="var(--organization-gap)" primaryWeight={1.32}
        lead={<PoolComputers state={resources} computerCount={company.computer_count} query={computersQuery.toString()}
          access={resourceAccess} onAccessError={checkResourceAccess} />}
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
        {canReadResources && !resourcesDenied && <Activity mode={resourceAccess === "available" ? "visible" : "hidden"}>
          <OrganizationResourceCharts companyId={company.id} overview={resources}
            onRead={onResourceRead} onAccessError={checkResourceAccess} />
        </Activity>}
      </WidgetLayout>
      {settings && <OrganizationSettings company={company}
        check={pendingCheck}
        onClose={() => setSettings(false)} onLeave={onLeave} onDelete={() => setDeleting(true)}
        onRenamed={onRenamed} onAppearanceChanged={onAppearanceChanged} onCheckPermissions={onMemberAccessError} />}
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
