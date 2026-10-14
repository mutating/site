import { useEffect } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { request, allPages } from "../lib/api";
import { useData } from "../lib/useData";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import { GiB, number } from "../lib/chart";
import type { Computer, Company } from "../lib/types";
import { Empty, ErrorBox, Heading, Loading } from "../components/UI";
export function Computers() {
  const { user } = useSession();
  const { t, locale } = useI18n();
  const [params, setParams] = useSearchParams();
  const companyId = params.get("company_id");
  const userId = params.get("user_id") || user!.id;
  const page = Math.max(
    1,
    Math.min(1_000_000, Number.parseInt(params.get("page") || "1", 10) || 1),
  );
  const company = useData(
    (signal) => allPages<Company>("companies", signal),
    user!.id,
  );
  const query = new URLSearchParams({
    ...(companyId ? { company_id: companyId } : { user_id: userId }),
    only_available: "true",
    limit: "10",
    offset: String((page - 1) * 10),
  });
  const queryKey = query.toString();
  const state = useData(
    async (signal) => ({
      ...(await request<Computer[]>(`computers?${queryKey}`, { signal })),
      queryKey,
    }),
    queryKey,
  );
  // A response from the preceding page must not correct a newer URL.
  const data = state.data?.queryKey === queryKey ? state.data : null;
  const updatePage = (value: number) => {
    const next = new URLSearchParams(params);
    next.set("page", String(value));
    setParams(next);
  };
  const totalPages = Math.max(1, Math.ceil((data?.total || 0) / 10));
  const outsidePages = !!data && page > totalPages;
  useEffect(() => {
    if (!state.loading && !state.error && outsidePages) {
      const next = new URLSearchParams(params);
      next.set("page", String(totalPages));
      setParams(next, { replace: true });
    }
  }, [state.loading, state.error, outsidePages, totalPages, params, setParams]);
  const label = companyId
    ? company.data?.find((c) => c.id === companyId)?.name || t("organizations")
    : t("personal");
  return (
    <>
      <Link className="back-link" to="/power-cells">
        ← {t("backPower")}
      </Link>
      <Heading index={`02 / POWER CELLS / ${label}`} title={t("computers")}>
        {t("onlineOnly")}
      </Heading>
      {state.loading || outsidePages || (!state.error && !data) ? (
        <Loading />
      ) : state.error ? (
        <ErrorBox error={state.error} retry={state.reload} />
      ) : (
        data && (
          <>
            <section className="table-panel">
              <div className="table-scroll">
                <table className="computer-table">
                  <thead>
                    <tr>
                      <th>{t("computer")}</th>
                      <th>{t("status")}</th>
                      <th>
                        CPU · {t("free")} / {t("total")}
                      </th>
                      <th>
                        {t("memory")} · {t("free")} / {t("total")}
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.data.map((computer) => (
                      <tr key={computer.id}>
                        <td>
                          <div className="computer-identity">
                            <strong>
                              {computer.name || computer.id.slice(0, 8)}
                            </strong>
                            <span className="status-badge mobile-status">
                              <span className="signal-dot" />
                              {t("online")}
                            </span>
                          </div>
                          <small>
                            {computer.os} · {computer.architecture} ·{" "}
                            {computer.runtime}
                          </small>
                        </td>
                        <td>
                          <span className="status-badge">
                            <span className="signal-dot" />
                            {t("online")}
                          </span>
                        </td>
                        <td
                          className="numeric"
                          data-label={`CPU · ${t("free")} / ${t("total")}`}
                        >
                          {number(
                            Math.max(
                              0,
                              computer.cpu_millis - computer.reserved_cpu,
                            ) / 1000,
                            locale,
                          )}{" "}
                          <span>
                            / {number(computer.cpu_millis / 1000, locale)}
                          </span>
                        </td>
                        <td
                          className="numeric"
                          data-label={`${t("memory")} · ${t("free")} / ${t("total")}`}
                        >
                          {number(
                            Math.max(
                              0,
                              computer.memory_bytes - computer.reserved_memory,
                            ) / GiB,
                            locale,
                          )}{" "}
                          <span>
                            / {number(computer.memory_bytes / GiB, locale)} GiB
                          </span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {!data.data.length && (
                <Empty icon="computer" title={t("noComputers")}>
                  {t("noComputersLead")}
                </Empty>
              )}
              <nav className="pagination" aria-label={t("page")}>
                <span>
                  {t("page")} {page} {t("of")} {totalPages}
                </span>
                <div>
                  <button
                    className="button secondary compact"
                    onClick={() => updatePage(page - 1)}
                    disabled={page <= 1}
                  >
                    ← {t("previous")}
                  </button>
                  <button
                    className="button secondary compact"
                    onClick={() => updatePage(page + 1)}
                    disabled={page >= totalPages}
                  >
                    {t("next")} →
                  </button>
                </div>
              </nav>
            </section>
            <p className="footnote">{t("resourcesNote")}</p>
          </>
        )
      )}
    </>
  );
}
