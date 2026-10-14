import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, json } from "../lib/api";
import { useData } from "../lib/useData";
import { useI18n } from "../lib/i18n";
import type { Invitation as InvitationData } from "../lib/types";
import { ErrorBox, Heading, Loading } from "../components/UI";
import { InvitationFacts } from "../components/InvitationUI";

export function Invitation() {
  const { token = "" } = useParams();
  const { t } = useI18n();
  const state = useData(
    (signal) =>
      api<InvitationData>(`invitations/link/${encodeURIComponent(token)}`, {
        signal,
      }),
    token,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [accepted, setAccepted] = useState(false);
  return (
    <>
      <Heading index={`04 / ${t("invitations")}`} title={t("invitations")} />
      {state.loading ? (
        <Loading />
      ) : state.error ? (
        <ErrorBox error={state.error} retry={state.reload} />
      ) : (
        state.data && (
          <section className="panel invitation-preview">
            <h2>{state.data.company_name}</h2>
            <InvitationFacts invitation={state.data} />
            <ErrorBox error={error} conflict="invitationConflict" />
            {accepted ? (
              <>
                <p className="success-message" role="status">
                  {t("invitationAccepted")}
                </p>
                <Link
                  className="button primary"
                  to={`/organizations/${state.data.company_id}`}
                >
                  {t("members")}
                </Link>
              </>
            ) : (
              <button
                className="button primary"
                disabled={busy}
                onClick={async () => {
                  if (busy) return;
                  setBusy(true);
                  setError(null);
                  try {
                    await api(
                      `invitations/link/${encodeURIComponent(token)}/accept`,
                      json("POST"),
                    );
                    setAccepted(true);
                  } catch (e) {
                    setError(e);
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                {t(busy ? "waiting" : "acceptInvitation")}
              </button>
            )}
          </section>
        )
      )}
    </>
  );
}
