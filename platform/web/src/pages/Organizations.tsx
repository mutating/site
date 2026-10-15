import { useState } from "react";
import { Link } from "react-router-dom";
import { api, allPages, json } from "../lib/api";
import { useData } from "../lib/useData";
import { useSession } from "../lib/session";
import { useI18n, type MessageKey } from "../lib/i18n";
import { filterOrganizations, organizationEntries } from "../lib/organizations";
import type { Company, Invitation } from "../lib/types";
import { CreateOrganizationDialog } from "../components/CreateOrganizationDialog";
import {
  ConfirmDialog,
  Empty,
  ErrorBox,
  Heading,
  Icon,
  Loading,
} from "../components/UI";

export function Organizations() {
  const { user } = useSession();
  const { t, locale } = useI18n();
  const state = useData(
    async (signal) => {
      const [companies, invitations] = await Promise.all([
        allPages<Company>("companies", signal),
        allPages<Invitation>("invitations", signal),
      ]);
      return { companies, invitations };
    },
    user!.id,
    true,
  );
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [message, setMessage] = useState<MessageKey | null>(null);
  const [busyInvitation, setBusyInvitation] = useState<string | null>(null);
  const [invitationError, setInvitationError] = useState<unknown>(null);
  const [action, setAction] = useState<{
    type: "delete" | "leave";
    company: Company;
  } | null>(null);
  const entries = organizationEntries(
    state.data?.companies ?? [],
    state.data?.invitations ?? [],
    user!.id,
  );
  const filtered = filterOrganizations(entries, query);
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
      state.update(
        (data) =>
          data && {
            ...data,
            invitations: data.invitations.filter(
              (row) => row.company_id !== invitation.company_id,
            ),
          },
      );
      setMessage(
        kind === "accept" ? "invitationAccepted" : "invitationDeclined",
      );
      state.reload();
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
        aria-busy={state.loading}
      >
        <header className="organization-directory-heading">
          <h2>
            {t("allOrganizations")}
            <span className="directory-count">
              {!state.data
                ? "—"
                : query.trim()
                  ? `${number(filtered.length)} / ${number(entries.length)}`
                  : number(entries.length)}
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
        {!state.data ? (
          state.loading ? (
            <Loading />
          ) : null
        ) : filtered.length ? (
          <div className="organization-list">
            {filtered.map((entry) => {
              const pending = entry.kind === "invitation";
              const row = pending ? entry.invitation : entry.company;
              const name = entry.name;
              const initials = name
                .trim()
                .split(/\s+/u)
                .slice(0, 2)
                .map((part) => Array.from(part)[0])
                .join("")
                .toLocaleUpperCase(locale);
              return (
                <article
                  className={`organization-row${pending ? " invitation-row" : ""}`}
                  key={`${entry.kind}:${entry.id}`}
                  aria-label={name}
                  data-kind={entry.kind}
                  data-group={entry.group}
                >
                  <span className="organization-monogram" aria-hidden="true">
                    {initials}
                  </span>
                  <div className="organization-info">
                    <h3>{name}</h3>
                    <small>
                      {pending
                        ? t(entry.invitation.role)
                        : t("sharedWorkspace")}
                    </small>
                  </div>
                  <span
                    className={`badge organization-role${pending ? " invitation-badge" : ""}`}
                  >
                    {pending
                      ? t("invitedOrganization")
                      : t(entry.company.my_role)}
                  </span>
                  {pending ? (
                    <>
                      <span
                        className="organization-metric organization-members-metric"
                        aria-label={`${t("members")}: ${number(row.member_count)}`}
                      >
                        <Icon name="users" size={16} />
                        <span>{number(row.member_count)}</span>
                      </span>
                      <span
                        className="organization-metric organization-computers-metric"
                        aria-label={`${t("computers")}: ${number(entry.computerCount)}`}
                      >
                        <Icon name="computer" size={16} />
                        <span data-testid="organization-computer-count">
                          {number(entry.computerCount)}
                        </span>
                      </span>
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
                    <>
                      <Link
                        className="organization-metric organization-members-metric"
                        aria-label={`${t("members")}: ${number(row.member_count)}`}
                        to={`/organizations/${entry.company.id}`}
                      >
                        <Icon name="users" size={16} />
                        <span>{number(row.member_count)}</span>
                      </Link>
                      <Link
                        className="organization-metric organization-computers-metric"
                        aria-label={`${t("computers")}: ${number(entry.computerCount)}`}
                        to={`/power-cells/computers?company_id=${entry.company.id}`}
                      >
                        <Icon name="computer" size={16} />
                        <span data-testid="organization-computer-count">
                          {number(entry.computerCount)}
                        </span>
                      </Link>
                      <div className="organization-actions">
                        {entry.company.my_role === "creator" && (
                          <button
                            className="text-button organization-icon-button danger-text"
                            aria-label={t("deleteOrganization")}
                            title={t("deleteOrganization")}
                            onClick={(event) => {
                              event.currentTarget.focus({
                                preventScroll: true,
                              });
                              setAction({
                                type: "delete",
                                company: entry.company,
                              });
                            }}
                          >
                            <Icon name="trash" size={16} />
                          </button>
                        )}
                        {entry.company.my_membership_id && (
                          <button
                            className="text-button organization-icon-button"
                            aria-label={t("leave")}
                            title={t("leave")}
                            onClick={(event) => {
                              event.currentTarget.focus({
                                preventScroll: true,
                              });
                              setAction({
                                type: "leave",
                                company: entry.company,
                              });
                            }}
                          >
                            <Icon name="exit" size={16} />
                          </button>
                        )}
                        <Link
                          className="organization-open"
                          aria-label={`${t("openOrganization")}: ${name}`}
                          to={`/organizations/${entry.company.id}`}
                        >
                          <Icon name="arrow" size={18} />
                        </Link>
                      </div>
                    </>
                  )}
                </article>
              );
            })}
          </div>
        ) : entries.length ? (
          <Empty icon="search" title={t("organizationsNoMatches")}>
            {t("organizationsNoMatchesLead")}
          </Empty>
        ) : (
          <Empty icon="users" title={t("organizationsEmptyTitle")}>
            {t("organizationsEmptyLead")}
          </Empty>
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
            state.update((data) => ({
              companies: [
                ...(data?.companies ?? []).filter(
                  (row) => row.id !== company.id,
                ),
                company,
              ],
              invitations: data?.invitations ?? [],
            }));
            setQuery("");
            setMessage("organizationCreated");
            state.reload();
          }}
        />
      )}
      {action && (
        <ConfirmDialog
          title={t(
            action.type === "delete" ? "deleteOrganization" : "confirmLeave",
          )}
          warning={t(
            action.type === "delete" ? "irreversible" : "leaveWarning",
          )}
          value={action.company.name}
          conflict={action.type === "leave" ? "leaveBlocked" : "blockedDelete"}
          onClose={() => setAction(null)}
          onConfirm={async () => {
            await api(
              action.type === "delete"
                ? `companies/${action.company.id}`
                : `memberships/${action.company.my_membership_id}`,
              json("DELETE"),
            );
            setMessage(null);
            state.update(
              (data) =>
                data && {
                  ...data,
                  companies: data.companies.filter(
                    (row) => row.id !== action.company.id,
                  ),
                },
            );
            state.reload();
          }}
        />
      )}
    </div>
  );
}
