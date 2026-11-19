import { useState, type FocusEvent } from "react";
import { allPages } from "../lib/api";
import { useData } from "../lib/useData";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import type { Company } from "../lib/types";
import { PoolCard, RestrictedPoolCard } from "../components/PoolCard";
import { ErrorBox, Heading, Icon, Loading } from "../components/UI";

function revealFocusedControl({ target }: FocusEvent<HTMLDivElement>) {
  if (!target.matches(":focus-visible")) return;
  const header = document.querySelector(".site-header");
  if (
    header && getComputedStyle(header).position === "sticky" &&
    target.getBoundingClientRect().top < header.getBoundingClientRect().bottom + 4
  ) {
    target.scrollIntoView({ block: "start", inline: "nearest" });
  }
}

export function Compute() {
  const { user } = useSession();
  const { t } = useI18n();
  const [revision, setRevision] = useState(0);
  const companies = useData(
    (signal) => allPages<Company>("companies", signal),
    user!.id,
    true,
    revision,
  );
  return (
    <>
      <Heading
        index={`02 / ${t("computeTag")}`}
        title={t("computeName")}
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
        {t("computeLead")}
      </Heading>
      <p className="resource-intro">{t("resourcesNote")}</p>
      <div className="pool-list compute-pools" onFocus={revealFocusedControl}>
        <PoolCard
          pool={{ id: user!.id, kind: "users", name: t("personal") }}
          chart
          revision={revision}
        />
        {companies.loading && !companies.data ? (
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
