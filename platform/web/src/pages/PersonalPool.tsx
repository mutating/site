import { useCallback, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { OrganizationHero } from "../components/OrganizationHero";
import { PersonalPoolResources } from "../components/OrganizationResources";
import { Icon } from "../components/UI";
import { revealFocusedComputeControl } from "../lib/compute";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";

export function PersonalPool() {
  const { t } = useI18n();
  const userId = useSession().user!.id;
  const connectedPoolOrigin = useLocation().state?.connectedPoolOrigin;
  const backState = connectedPoolOrigin ? { connectedPoolOrigin } : undefined;
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision((value) => value + 1), []);
  const query = new URLSearchParams({ user_id: userId, only_available: "false" });
  const name = t("personalPool");

  return (
    <>
      <div className="personal-pool-links" onFocus={revealFocusedComputeControl}>
        <Link className="back-link compute-back-link" to="/compute" state={backState}>← {t("backCompute")}</Link>
        <Link className="back-link compute-back-link" to="/compute/pools" state={backState}>← {t("backPools")}</Link>
      </div>
      <section className="organization-detail personal-pool-detail" aria-label={name} onFocus={revealFocusedComputeControl}>
        <OrganizationHero
          name={name}
          context={t("computeName")}
          description={t("personalWorkspace")}
          action={<button type="button" className="button secondary compact organization-hero-action" onClick={refresh}>
            <Icon name="refresh" size={16} /> {t("refresh")}
          </button>}
        />
        <PersonalPoolResources userId={userId} query={query.toString()} revision={revision} />
      </section>
    </>
  );
}
