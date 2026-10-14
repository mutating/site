import { Link } from "react-router-dom";
import { allPages } from "../lib/api";
import { useData } from "../lib/useData";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import type { Company } from "../lib/types";
import { PoolCard, RestrictedPoolCard } from "../components/PoolCard";
import { ErrorBox, Heading, Icon, Loading } from "../components/UI";
export function Overview() {
  const { user } = useSession();
  const { t } = useI18n();
  const companies = useData(
    (signal) => allPages<Company>("companies", signal),
    user!.id,
  );
  const name = user!.first_name || user!.nickname || user!.email;
  return (
    <>
      <Heading index={`01 / ${t("home")}`} title={`${t("hello")}, ${name}`}>
        {t("overviewLead")}
      </Heading>
      <div className="product-grid">
        <Link className="product-card power-product" to="/power-cells">
          <span className="eyebrow">01 / {t("powerTag")}</span>
          <div className="product-card-title">
            <h2>Power Cells</h2>
            <Icon name="cells" size={50} />
          </div>
          <p>{t("powerDescription")}</p>
          <span className="product-arrow">
            <Icon name="arrow" />
          </span>
        </Link>
        <Link className="product-card cloud-product" to="/cloud">
          <span className="eyebrow">02 / {t("cloudTag")}</span>
          <div className="product-card-title">
            <h2>{t("cloud")}</h2>
            <Icon name="cloud" size={50} />
          </div>
          <p>{t("cloudLead")}</p>
          <span className="badge">{t("soon")}</span>
        </Link>
      </div>
      <div className="section-heading">
        <h2>{t("organizations")}</h2>
        <Link className="text-link" to="/organizations">
          {t("manageOrganizations")} ↗
        </Link>
      </div>
      {companies.loading ? (
        <Loading />
      ) : companies.error ? (
        <ErrorBox error={companies.error} retry={companies.reload} />
      ) : companies.data?.length ? (
        <>
          <p className="muted section-lede">{t("organizationLead")}</p>
          <div className="pool-list">
            {companies.data.map((company) =>
              company.my_permissions.includes("resources.read") ? (
                <PoolCard
                  key={company.id}
                  pool={{ ...company, kind: "companies" }}
                />
              ) : (
                <RestrictedPoolCard key={company.id} company={company} />
              ),
            )}
          </div>
          <p className="footnote">{t("resourcesNote")}</p>
        </>
      ) : (
        <div className="small-empty">
          <Icon name="users" size={28} />
          <p>{t("noOrganizations")}</p>
          <Link className="text-link" to="/organizations">
            {t("createOrganization")} ↗
          </Link>
        </div>
      )}
    </>
  );
}
