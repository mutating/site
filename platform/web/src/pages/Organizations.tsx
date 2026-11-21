import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { api, json } from "../lib/api";
import { useReloadOnComputerPoolChange } from "../lib/compute";
import { notifyOrganizationChange, useOrganizationDirectory } from "../lib/useOrganizationDirectory";
import { useSession } from "../lib/session";
import { useI18n, type MessageKey } from "../lib/i18n";
import { organizationImagePath } from "../lib/organizationImages";
import {
  ORGANIZATIONS_PAGE_SIZE,
  organizationPage,
  organizationSearchParams,
} from "../lib/organizations";
import type { Company, Invitation } from "../lib/types";
import { CreateOrganizationDialog } from "../components/CreateOrganizationDialog";
import { OrganizationRowContent } from "../components/OrganizationRowContent";
import { Empty, ErrorBox, Heading, Icon, Loading } from "../components/UI";

export function Organizations() {
  const userId = useSession().user!.id;
  const { key: locationKey, state: routeState } = useLocation();
  const connectedPoolOrigin = routeState?.connectedPoolOrigin;
  const poolNavigationState = connectedPoolOrigin ? { connectedPoolOrigin } : undefined;
  const { t, locale } = useI18n();
  const [params, setParams] = useSearchParams();
  const query = params.get("q") || "";
  const page = organizationPage(params);
  const state = useOrganizationDirectory(userId, query, page);
  useReloadOnComputerPoolChange(userId, state.reload);
  const activeView = useRef<{ userId: string; locationKey: string } | null>(null);
  useLayoutEffect(() => {
    activeView.current = { userId, locationKey };
    return () => { activeView.current = null; };
  }, [userId, locationKey]);
  const setQuery = (value: string) =>
    setParams(organizationSearchParams(params, value), { replace: true, state: poolNavigationState });
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState<MessageKey | null>(null);
  const [busyInvitation, setBusyInvitation] = useState<string | null>(null);
  const [invitationError, setInvitationError] = useState<unknown>(null);
  const entries = state.data?.entries ?? [];
  const totalPages = Math.max(
    1,
    Math.ceil((state.data?.total ?? 0) / ORGANIZATIONS_PAGE_SIZE),
  );
  const outsidePages = !!state.data && page > totalPages;
  const updatePage = (value: number, replace = false) => {
    const next = new URLSearchParams(params);
    if (value === 1) next.delete("page");
    else next.set("page", String(value));
    setParams(next, { replace, state: poolNavigationState });
  };
  useEffect(() => {
    if (!state.loading && !state.error && outsidePages)
      updatePage(totalPages, true);
  }, [state.loading, state.error, outsidePages, totalPages, params, setParams, connectedPoolOrigin]);
  const number = (value: number) => value.toLocaleString(locale);

  async function invitationAction(
    invitation: Invitation,
    kind: "accept" | "decline",
  ) {
    if (busyInvitation) return;
    setBusyInvitation(invitation.id);
    setInvitationError(null);
    setMessage(null);
    try {
      await api(`invitations/${invitation.id}/${kind}`, json("POST"));
      state.remove(invitation.company_id);
      setMessage(
        kind === "accept" ? "invitationAccepted" : "invitationDeclined",
      );
      if (kind === "accept") {
        notifyOrganizationChange({ type: "membersChanged", userId, companyId: invitation.company_id, source: Symbol() });
      } else {
        state.reload();
      }
    } catch (error) {
      setInvitationError(error);
    } finally {
      setBusyInvitation(null);
    }
  }

  return (
    <div className="organizations-page">
      <Heading
        index={`04 / ${t("organizations")}`}
        title={t("organizations")}
        action={
          <button
            className="button primary create-organization-trigger"
            onClick={(event) => {
              event.currentTarget.focus({ preventScroll: true });
              setMessage(null);
              setCreating(true);
            }}
          >
            <Icon name="plus" size={17} />
            {t("createOrganization")}
          </button>
        }
      >
        {t("organizationsLead")}
      </Heading>
      <section
        className="organization-directory"
        aria-label={t("allOrganizations")}
        aria-busy={state.loading || outsidePages}
      >
        <header className="organization-directory-heading">
          <h2>
            {t("allOrganizations")}
            <span className="directory-count">
              {!state.data
                ? "—"
                : query.trim()
                  ? `${number(state.data.total)} / ${number(state.data.totalAll)}`
                  : number(state.data.total)}
            </span>
          </h2>
        </header>
        <div className="organization-search">
          <Icon name="search" size={19} />
          <input
            type="search"
            aria-label={t("searchOrganizations")}
            placeholder={t("searchOrganizations")}
            value={query}
            maxLength={200}
            onChange={(event) => setQuery(event.target.value)}
          />
          {query && (
            <button
              type="button"
              className="text-button"
              aria-label={t("clearSearch")}
              onClick={() => setQuery("")}
            >
              <Icon name="close" size={16} />
            </button>
          )}
        </div>
        {message && (
          <p className="success-message" role="status">
            {t(message)}
          </p>
        )}
        <ErrorBox error={invitationError} conflict="invitationConflict" />
        <ErrorBox error={state.error} retry={state.reload} />
        {!state.data || outsidePages ? (
          state.loading ? (
            <Loading />
          ) : null
        ) : entries.length ? (
          <div className="organization-list">
            {entries.map((entry) => {
              const pending = entry.kind === "invitation";
              const row = pending ? entry.invitation : entry.company;
              const name = entry.name;
              const content = (
                <OrganizationRowContent
                  name={name}
                  subtitle={pending ? t(entry.invitation.role) : t("sharedWorkspace")}
                  role={pending ? t("invitedOrganization") : t(entry.company.my_role)}
                  avatarPath={pending ? null : organizationImagePath(entry.company, "avatar")}
                  memberCount={row.member_count}
                  computerCount={entry.computerCount}
                  invited={pending}
                />
              );
              return (
                <article
                  className={`organization-row${pending ? " invitation-row" : ""}`}
                  key={`${entry.kind}:${entry.id}`}
                  aria-label={name}
                  data-kind={entry.kind}
                  data-group={entry.group}
                >
                  {pending ? (
                    <>
                      {content}
                      <div className="invitation-actions">
                        <button
                          className="button primary compact"
                          disabled={!!busyInvitation}
                          onClick={() =>
                            invitationAction(entry.invitation, "accept")
                          }
                        >
                          {t(
                            busyInvitation === entry.id
                              ? "waiting"
                              : "acceptInvitation",
                          )}
                        </button>
                        <button
                          className="text-button"
                          disabled={!!busyInvitation}
                          onClick={() =>
                            invitationAction(entry.invitation, "decline")
                          }
                        >
                          {t("declineInvitation")}
                        </button>
                      </div>
                    </>
                  ) : (
                    <Link
                      className="organization-row-link"
                      aria-label={`${t("openOrganization")}: ${name}`}
                      to={`/organizations/${entry.company.id}`}
                      state={{
                        directorySearch: params.size ? `?${params}` : "",
                      }}
                    >
                      {content}
                    </Link>
                  )}
                </article>
              );
            })}
          </div>
        ) : query.trim() ? (
          <Empty icon="search" title={t("organizationsNoMatches")}>
            {t("organizationsNoMatchesLead")}
          </Empty>
        ) : (
          <Empty icon="users" title={t("organizationsEmptyTitle")}>
            {t("organizationsEmptyLead")}
          </Empty>
        )}
        {state.data &&
          !outsidePages &&
          (state.data.total > ORGANIZATIONS_PAGE_SIZE || page > 1) && (
            <nav className="pagination" aria-label={t("page")}>
              <span>
                {t("page")} {page} {t("of")} {totalPages}
              </span>
              <div>
                <button
                  className="button secondary compact"
                  disabled={state.loading || page <= 1}
                  onClick={() => updatePage(page - 1)}
                >
                  ← {t("previous")}
                </button>
                <button
                  className="button secondary compact"
                  disabled={state.loading || page >= totalPages}
                  onClick={() => updatePage(page + 1)}
                >
                  {t("next")} →
                </button>
              </div>
            </nav>
          )}
      </section>
      {creating && (
        <CreateOrganizationDialog
          onClose={() => setCreating(false)}
          onCreate={async (name) => {
            const company = await api<Company>(
              "companies",
              json("POST", { name }),
            );
            const current = activeView.current;
            if (current?.userId === userId) {
              if (current.locationKey === locationKey) {
                state.insert(company);
                setQuery("");
              }
              setMessage("organizationCreated");
            }
            notifyOrganizationChange({ type: "created", userId });
          }}
        />
      )}
    </div>
  );
}
