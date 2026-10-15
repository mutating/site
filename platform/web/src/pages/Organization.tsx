import { useEffect, useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { api, apiURL, allPages, json, request } from "../lib/api";
import { useData } from "../lib/useData";
import { useSession } from "../lib/session";
import { useI18n, type MessageKey } from "../lib/i18n";
import { memberName } from "../lib/permissions";
import type {
  Company,
  CreatedInvitation,
  Invitation,
  Membership,
} from "../lib/types";
import { ConfirmDialog, ErrorBox, Heading, Loading } from "../components/UI";
import { MemberSharing } from "../components/MemberSharing";
import { MemberEditor } from "../components/MemberEditor";
import {
  InvitationFacts,
  PermissionSummary,
  ShareLink,
} from "../components/InvitationUI";

export function Organization() {
  const { companyId = "" } = useParams();
  const state = useData(
    (signal) => api<Company>(`companies/${companyId}`, { signal }),
    companyId,
    true,
  );
  const { t } = useI18n();
  return (
    <>
      <Link className="back-link" to="/organizations">
        ← {t("backOrganizations")}
      </Link>
      <ErrorBox error={state.error} retry={state.reload} />
      {state.data?.id === companyId ? (
        <OrganizationContent
          key={companyId}
          company={state.data}
          onChanged={state.reload}
        />
      ) : state.loading ? (
        <Loading />
      ) : null}
    </>
  );
}
function OrganizationContent({
  company,
  onChanged,
}: {
  company: Company;
  onChanged: () => void;
}) {
  const { t } = useI18n();
  const { user } = useSession();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const page = Math.max(
    1,
    Math.min(1_000_000, parseInt(params.get("page") || "1", 10) || 1),
  );
  const canRead = company.my_permissions.includes("members.read"),
    canManage = company.my_permissions.includes("members.manage");
  const memberKey = `${company.id}/${page}/${canRead}`;
  const members = useData(
    async (signal) => ({
      ...(canRead
        ? await request<Membership[]>(
            `memberships?company_id=${company.id}&limit=10&offset=${(page - 1) * 10}`,
            { signal },
          )
        : { data: [], total: 0 }),
      key: memberKey,
    }),
    memberKey,
  );
  const data = members.data?.key === memberKey ? members.data : null;
  const invitations = useData(
    (signal) =>
      canManage
        ? allPages<Invitation>(`invitations?company_id=${company.id}`, signal)
        : Promise.resolve([]),
    `${company.id}/${canManage}`,
  );
  const [editing, setEditing] = useState<Membership | null>(null);
  const [removing, setRemoving] = useState<Membership | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [linkRevision, setLinkRevision] = useState(0);
  const [inviteBusy, setInviteBusy] = useState<string | null>(null);
  const [inviteError, setInviteError] = useState<unknown>(null);
  const [renameError, setRenameError] = useState<unknown>(null);
  const [renameBusy, setRenameBusy] = useState(false);
  const [renamed, setRenamed] = useState(false);
  const pages = Math.max(1, Math.ceil((data?.total || 0) / 10));
  const updatePage = (value: number, replace = false) => {
    const next = new URLSearchParams(params);
    next.set("page", String(value));
    setParams(next, { replace });
  };
  useEffect(() => {
    if (data && !members.loading && page > pages) updatePage(pages, true);
  }, [data, members.loading, page, pages]);
  const refresh = () => {
    members.reload();
    invitations.reload();
    onChanged();
  };
  async function inviteAction(
    invitation: Invitation,
    action: "link" | "revoke",
  ) {
    if (inviteBusy) return;
    setInviteBusy(invitation.id);
    setInviteError(null);
    try {
      if (action === "link") {
        const result = await api<CreatedInvitation>(
          `invitations/${invitation.id}/link`,
          json("POST"),
        );
        setToken(result.token);
      } else {
        await api(`invitations/${invitation.id}`, json("DELETE"));
        setToken(null);
      }
      invitations.reload();
      setLinkRevision((value) => value + 1);
    } catch (e) {
      setInviteError(e);
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
  return (
    <>
      <Heading index={`04 / ${t("organizations")}`} title={company.name}>
        <span>
          {t("membersLead")} · {t(company.my_role)}
        </span>
      </Heading>
      {canManage && (
        <MemberSharing
          company={company}
          onChanged={refresh}
          linkRevision={linkRevision}
        />
      )}
      <section className="panel members-panel" aria-label={t("members")}>
        <h2>{t("members")}</h2>
        {!canRead ? (
          <p className="muted">{t("memberReadDenied")}</p>
        ) : members.error ? (
          <ErrorBox error={members.error} retry={members.reload} />
        ) : members.loading || !data || page > pages ? (
          <Loading />
        ) : (
          <>
            <div className="member-list">
              {data.data.map((member) => (
                <article
                  className="member-row"
                  key={member.id}
                  aria-label={memberName(member.user)}
                >
                  <div className="member-identity">
                    <span className="avatar">
                      {member.user.avatar_version ? (
                        <img
                          src={apiURL(`memberships/${member.id}/avatar?v=${member.user.avatar_version}`)}
                          alt=""
                        />
                      ) : (
                        memberName(member.user).slice(0, 1).toUpperCase()
                      )}
                    </span>
                    <div>
                      <h3>{memberName(member.user)}</h3>
                      {member.user.nickname && (
                        <small>@{member.user.nickname}</small>
                      )}
                      <span className="badge">{t(member.role)}</span>
                    </div>
                  </div>
                  <PermissionSummary values={member.effective_permissions} />
                  <div className="member-actions">
                    {canManage &&
                      (company.my_role === "creator" ||
                        member.role === "user") && (
                        <button
                          className="button secondary compact"
                          onClick={(event) => {
                            event.currentTarget.focus({ preventScroll: true });
                            setEditing(member);
                          }}
                        >
                          {t("editMember")}
                        </button>
                      )}
                    {(member.user_id === user!.id ||
                      (canManage &&
                        (company.my_role === "creator" ||
                          member.role === "user"))) && (
                      <button
                        className="text-button danger-text"
                        onClick={(event) => {
                          event.currentTarget.focus({ preventScroll: true });
                          setRemoving(member);
                        }}
                      >
                        {t(
                          member.user_id === user!.id
                            ? "leave"
                            : "removeMember",
                        )}
                      </button>
                    )}
                  </div>
                </article>
              ))}
            </div>
            <nav className="pagination" aria-label={t("page")}>
              <span>
                {t("page")} {page} {t("of")} {pages}
              </span>
              <div>
                <button
                  className="button secondary compact"
                  disabled={page <= 1}
                  onClick={() => updatePage(page - 1)}
                >
                  ← {t("previous")}
                </button>
                <button
                  className="button secondary compact"
                  disabled={page >= pages}
                  onClick={() => updatePage(page + 1)}
                >
                  {t("next")} →
                </button>
              </div>
            </nav>
          </>
        )}
      </section>
      {canManage && (
        <section
          className="panel outgoing-invitations"
          aria-label={t("invitations")}
        >
          <h2>{t("invitations")}</h2>
          <p className="field-help">{t("renewLinkHelp")}</p>
          <ErrorBox error={inviteError} conflict="invitationConflict" />
          {invitations.loading ? (
            <Loading />
          ) : invitations.error ? (
            <ErrorBox error={invitations.error} retry={invitations.reload} />
          ) : !invitations.data?.length ? (
            <p className="muted">{t("noInvitations")}</p>
          ) : (
            invitations.data.map((invitation) => (
              <article
                key={invitation.id}
                className="invitation-row"
                aria-label={invitation.email || t("openInvite")}
              >
                <h3>{invitation.email || t("openInvite")}</h3>
                <span className="badge">{t(statuses[invitation.status])}</span>
                <InvitationFacts invitation={invitation} />
                {invitation.status === "pending" &&
                  (company.my_role === "creator" ||
                    invitation.role === "user") && (
                    <div className="form-actions">
                      <button
                        className="button secondary compact"
                        disabled={!!inviteBusy}
                        onClick={() => inviteAction(invitation, "link")}
                      >
                        {t("renewInvitationLink")}
                      </button>
                      <button
                        className="text-button danger-text"
                        disabled={!!inviteBusy}
                        onClick={() => inviteAction(invitation, "revoke")}
                      >
                        {t("revokeInvitation")}
                      </button>
                    </div>
                  )}
              </article>
            ))
          )}
          {token && <ShareLink key={token} token={token} />}
        </section>
      )}
      {company.my_permissions.includes("company.manage") && (
        <section className="panel organization-settings">
          <h2>{t("organizationSettings")}</h2>
          <form
            onSubmit={async (event) => {
              event.preventDefault();
              if (renameBusy) return;
              const name = String(
                new FormData(event.currentTarget).get("name") || "",
              ).trim();
              if (!name) return;
              setRenameBusy(true);
              setRenameError(null);
              setRenamed(false);
              try {
                await api(`companies/${company.id}`, json("PATCH", { name }));
                setRenamed(true);
                onChanged();
              } catch (e) {
                setRenameError(e);
              } finally {
                setRenameBusy(false);
              }
            }}
          >
            <label className="field">
              {t("organizationName")}
              <input
                name="name"
                defaultValue={company.name}
                required
                maxLength={200}
                disabled={renameBusy}
              />
            </label>
            <ErrorBox error={renameError} />
            {renamed && (
              <p className="success-message" role="status">
                {t("saved")}
              </p>
            )}
            <button className="button primary" disabled={renameBusy}>
              {t(renameBusy ? "waiting" : "save")}
            </button>
          </form>
        </section>
      )}
      {editing && (
        <MemberEditor
          key={editing.id}
          member={editing}
          company={company}
          onClose={() => setEditing(null)}
          onSaved={refresh}
        />
      )}
      {removing && (
        <ConfirmDialog
          title={t(
            removing.user_id === user!.id ? "confirmLeave" : "removeMember",
          )}
          value={memberName(removing.user)}
          warning={t(
            removing.user_id === user!.id
              ? "leaveWarning"
              : "removeMemberWarning",
          )}
          conflict="memberConflict"
          onClose={() => setRemoving(null)}
          onConfirm={async () => {
            await api(`memberships/${removing.id}`, json("DELETE"));
            if (removing.user_id === user!.id) navigate("/organizations");
            else refresh();
          }}
        />
      )}
    </>
  );
}
