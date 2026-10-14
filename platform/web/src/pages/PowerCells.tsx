import { useState } from "react";
import { allPages } from "../lib/api";
import { useData } from "../lib/useData";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import type { Company } from "../lib/types";
import { PoolCard, RestrictedPoolCard } from "../components/PoolCard";
import { ErrorBox, Heading, Icon, Loading } from "../components/UI";
export function PowerCells() {
  const { user } = useSession();
  const { t } = useI18n();
  const [revision, setRevision] = useState(0);
  const companies = useData(
    (signal) => allPages<Company>("companies", signal),
    `${user!.id}/${revision}`,
  );
  return (
    <>
      <Heading
        index={`02 / ${t("powerTag")}`}
        title="Power Cells"
        action={
          <button
            className="button secondary compact"
            onClick={() => setRevision((x) => x + 1)}
          >
            <Icon name="refresh" size={16} />
            {t("refresh")}
          </button>
        }
      >
        {t("powerLead")}
      </Heading>
      <p className="resource-intro">{t("resourcesNote")}</p>
      <div className="pool-list">
        <PoolCard
          pool={{ id: user!.id, kind: "users", name: t("personal") }}
          chart
          revision={revision}
        />
        {companies.loading ? (
          <Loading />
        ) : companies.error ? (
          <ErrorBox error={companies.error} retry={companies.reload} />
        ) : (
          companies.data?.map((company) =>
            company.my_permissions.includes("resources.read") ? (
              <PoolCard
                key={company.id}
                pool={{ ...company, kind: "companies" }}
                chart
                revision={revision}
              />
            ) : (
              <RestrictedPoolCard key={company.id} company={company} />
            ),
          )
        )}
      </div>
    </>
  );
}
