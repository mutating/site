import { useRef, useState } from "react";
import { api, allPages, json } from "../lib/api";
import { useData } from "../lib/useData";
import { useI18n } from "../lib/i18n";
import { permissionLabels } from "../lib/permissions";
import type { Invitation, Permission } from "../lib/types";
import { ErrorBox, Loading } from "./UI";

export function PermissionSummary({ values }: { values: Permission[] }) {
  const { t } = useI18n();
  return values.length ? (
    <ul className="permission-summary">
      {values.map((value) => (
        <li key={value}>{t(permissionLabels[value])}</li>
      ))}
    </ul>
  ) : (
    <p className="field-help">{t("noPermissions")}</p>
  );
}
export function InvitationFacts({ invitation }: { invitation: Invitation }) {
  const { t, locale } = useI18n();
  return (
    <>
      <p className="muted">
        {t("memberRole")}: <strong>{t(invitation.role)}</strong>
      </p>
      <p className="muted invitation-expiry">
        {t("expiresOn")}{" "}
        <time dateTime={invitation.expires_at}>
          {new Date(invitation.expires_at).toLocaleDateString(locale)}
        </time>
      </p>
      <PermissionSummary values={invitation.effective_permissions} />
    </>
  );
}
export function ShareLink({ token }: { token: string }) {
  const { t } = useI18n();
  const ref = useRef<HTMLInputElement>(null);
  const [copied, setCopied] = useState(false);
  const [selected, setSelected] = useState(false);
  const value = `${window.location.origin}/invitations/${encodeURIComponent(token)}`;
  return (
    <div className="share-link panel" key={token}>
      <label className="field">
        {t("invitationLink")}
        <input
          ref={ref}
          value={value}
          readOnly
          onFocus={(event) => event.currentTarget.select()}
        />
      </label>
      <button
        className="button secondary"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
          } catch {
            ref.current?.focus();
            ref.current?.select();
            setSelected(true);
          }
        }}
      >
        {t("copyLink")}
      </button>
      {(copied || selected) && (
        <p role="status" className="success-message">
          {t(copied ? "copiedLink" : "selectLink")}
        </p>
      )}
    </div>
  );
}
export function IncomingInvitations({
  onAccepted,
}: {
  onAccepted: () => void;
}) {
  const { t } = useI18n();
  const state = useData(
    (signal) => allPages<Invitation>("invitations", signal),
    "incoming",
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState<
    "invitationAccepted" | "invitationDeclined" | null
  >(null);
  async function act(id: string, action: "accept" | "decline") {
    if (busy) return;
    setBusy(id);
    setError(null);
    setMessage(null);
    try {
      await api(`invitations/${id}/${action}`, json("POST"));
      state.reload();
      setMessage(
        action === "accept" ? "invitationAccepted" : "invitationDeclined",
      );
      if (action === "accept") onAccepted();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }
  if (state.loading) return <Loading />;
  if (state.error) return <ErrorBox error={state.error} retry={state.reload} />;
  if (!state.data?.length && !message && !error) return null;
  return (
    <section
      className="incoming-invitations panel"
      aria-label={t("incomingInvitations")}
    >
      <h2>{t("incomingInvitations")}</h2>
      <ErrorBox error={error} conflict="invitationConflict" />
      {message && (
        <p role="status" className="success-message">
          {t(message)}
        </p>
      )}
      {state.data?.map((invitation) => (
        <article
          className="invitation-row"
          key={invitation.id}
          aria-label={invitation.company_name}
        >
          <h3>{invitation.company_name}</h3>
          <InvitationFacts invitation={invitation} />
          <div className="form-actions">
            <button
              className="button primary"
              disabled={!!busy}
              onClick={() => act(invitation.id, "accept")}
            >
              {t(busy === invitation.id ? "waiting" : "acceptInvitation")}
            </button>
            <button
              className="button secondary"
              disabled={!!busy}
              onClick={() => act(invitation.id, "decline")}
            >
              {t("declineInvitation")}
            </button>
          </div>
        </article>
      ))}
    </section>
  );
}
