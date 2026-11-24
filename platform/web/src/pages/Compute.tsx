import { Link } from "react-router-dom";
import { ErrorBox, Heading, Icon } from "../components/UI";
import { api } from "../lib/api";
import { useComputeCompanies } from "../lib/compute";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";
import type { ConnectedComputerPage } from "../lib/types";
import { useData, useRefreshWhileVisible } from "../lib/useData";

export function Compute() {
  const { user } = useSession();
  const { t, locale } = useI18n();
  const companies = useComputeCompanies(user!.id);
  const computers = useData(
    (signal) => api<ConnectedComputerPage>("connected-computers?limit=1", { signal }),
    user!.id,
    true,
    { retainError: true },
  );
  useRefreshWhileVisible(computers.reload);
  const format = (value?: number) => value?.toLocaleString(locale) ?? "—";
  const data = computers.error ? null : computers.data;
  const total = data?.total;
  const online = data?.online;
  const offline = data ? data.total - data.online : undefined;
  const organizationCount = companies.data?.length;
  return (
    <>
      <Heading title={t("computeName")} />
      <div className="compute-overview">
        <dl className="compute-status-summary" aria-busy={computers.loading}>
          <div>
            <dt>{t("myComputers")}</dt>
            <dd>{format(total)}</dd>
          </div>
          <div>
            <dt>{t("online")}</dt>
            <dd>{format(online)}</dd>
          </div>
          <div>
            <dt>{t("connectionOffline")}</dt>
            <dd>{format(offline)}</dd>
          </div>
        </dl>
        <ErrorBox error={computers.error} retry={computers.reload} />
        <ErrorBox error={companies.error} retry={companies.reload} />
        <div className="compute-destinations">
          <Link className="compute-destination" to="/compute/computers" aria-label={t("connectedComputers")}>
            <span className="compute-destination-icon"><Icon name="computer" /></span>
            <h2>{t("connectedComputers")}</h2>
            <p>{t("connectedComputersEntry")}</p>
            <div className="compute-destination-footer">
              <div><strong>{format(total)}</strong> <span>{t("total")} · {format(online)} {t("online").toLocaleLowerCase(locale)}</span></div>
              <Icon name="arrow" size={17} />
            </div>
          </Link>
          <Link className="compute-destination" to="/compute/pools" aria-label={t("computePools")}>
            <span className="compute-destination-icon"><Icon name="users" /></span>
            <h2>{t("computePools")}</h2>
            <p>{t("computePoolsEntry")}</p>
            <div className="compute-destination-footer">
              <div>
                <strong>{format(organizationCount === undefined ? undefined : organizationCount + 1)}</strong>{" "}
                <span>{t("personalPool")} · {t("organizations")} {format(organizationCount)}</span>
              </div>
              <Icon name="arrow" size={17} />
            </div>
          </Link>
        </div>
      </div>
    </>
  );
}
