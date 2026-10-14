import { useState } from "react";
import { Link } from "react-router-dom";
import { api, allPages, json } from "../lib/api";
import { useData } from "../lib/useData";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import type { Company } from "../lib/types";
import { IncomingInvitations } from "../components/InvitationUI";
import {
  ConfirmDialog,
  Empty,
  ErrorBox,
  Heading,
  Icon,
  Loading,
} from "../components/UI";
type Organization = Company;
export function Organizations() {
  const { user } = useSession();
  const { t } = useI18n();
  const state = useData(
    (signal) => allPages<Company>("companies", signal),
    user!.id,
  );
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [success, setSuccess] = useState(false);
  const [action, setAction] = useState<{
    type: "delete" | "leave";
    company: Organization;
  } | null>(null);
  function cards(rows: Organization[]) {
    return (
      <div className="organization-list">
        {rows.map((company) => (
          <article className="organization-row" key={company.id}>
            <span className="pool-icon">
              <Icon name="users" />
            </span>
            <div className="organization-info">
              <h3>{company.name}</h3>
              <span className="badge">{t(company.my_role)}</span>
            </div>
            <Link
              className="text-link"
              to={`/power-cells/computers?company_id=${company.id}`}
            >
              {t("computers")} ↗
            </Link>
            <div className="organization-actions">
              <Link className="text-link" to={`/organizations/${company.id}`}>
                {t("members")} ↗
              </Link>
              {company.my_role === "creator" && (
                <button
                  className="text-button danger-text"
                  onClick={(event) => {
                    event.currentTarget.focus({ preventScroll: true });
                    setAction({ type: "delete", company });
                  }}
                >
                  {t("deleteOrganization")}
                </button>
              )}
              {company.my_membership_id && (
                <button
                  className="text-button"
                  onClick={(event) => {
                    event.currentTarget.focus({ preventScroll: true });
                    setAction({ type: "leave", company });
                  }}
                >
                  {t("leave")}
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
    );
  }
  return (
    <>
      <Heading index={`04 / ${t("organizations")}`} title={t("organizations")}>
        {t("organizationsLead")}
      </Heading>
      <IncomingInvitations onAccepted={state.reload} />
      <section className="create-organization panel">
        <h2>{t("createOrganization")}</h2>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (!name.trim() || busy) return;
            setBusy(true);
            setError(null);
            setSuccess(false);
            try {
              await api("companies", json("POST", { name: name.trim() }));
              setName("");
              state.reload();
              setSuccess(true);
            } catch (err) {
              setError(err);
            } finally {
              setBusy(false);
            }
          }}
        >
          <label className="field">
            {t("organizationName")}
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={200}
              required
            />
          </label>
          <button className="button primary" disabled={busy || !name.trim()}>
            <Icon name="plus" size={18} />
            {t(busy ? "waiting" : "createOrganization")}
          </button>
        </form>
        <ErrorBox error={error} />
        {success && (
          <p className="success-message" role="status">
            {t("organizationCreated")}
          </p>
        )}
      </section>
      {state.loading ? (
        <Loading />
      ) : state.error ? (
        <ErrorBox error={state.error} retry={state.reload} />
      ) : (
        state.data && (
          <>
            <div className="section-heading">
              <h2>{t("createdOrganizations")}</h2>
            </div>
            {state.data.some((c) => c.created_by_user_id === user!.id) ? (
              cards(state.data.filter((c) => c.created_by_user_id === user!.id))
            ) : (
              <p className="small-empty">{t("noCreatedOrganizations")}</p>
            )}
            {state.data.some((c) => c.created_by_user_id !== user!.id) && (
              <>
                <div className="section-heading">
                  <h2>{t("joinedOrganizations")}</h2>
                </div>
                {cards(
                  state.data.filter((c) => c.created_by_user_id !== user!.id),
                )}
              </>
            )}
            {!state.data.length && (
              <Empty icon="users" title={t("noOrganizations")} />
            )}
          </>
        )
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
            setSuccess(false);
            state.reload();
          }}
        />
      )}
    </>
  );
}
