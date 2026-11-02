import type { RefObject } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { api } from "../lib/api";
import { useData } from "../lib/useData";
import { useI18n } from "../lib/i18n";
import { GiB, number } from "../lib/chart";
import type { Computer as ComputerData } from "../lib/types";
import { Benchmarks } from "../components/Benchmarks";
import { ErrorBox, Heading, Icon, Loading } from "../components/UI";

export function Computer({ interrupted }: { interrupted: RefObject<boolean> }) {
  const { computerId } = useParams();
  return <ComputerView key={computerId} computerId={computerId!} interrupted={interrupted} />;
}

function ComputerView({ computerId, interrupted }: {
  computerId: string;
  interrupted: RefObject<boolean>;
}) {
  const { t, locale } = useI18n();
  const [params] = useSearchParams();
  const back = new URLSearchParams(params);
  back.delete("benchmark");
  const state = useData(
    (signal) =>
      api<ComputerData>(`computers/${encodeURIComponent(computerId)}`, {
        signal,
      }),
    computerId,
  );
  const reload = () => { interrupted.current = false; state.reload(); };
  const computer = state.data;
  return (
    <>
      <Link
        className="back-link"
        to={`/power-cells/computers${back.size ? `?${back}` : ""}`}
      >
        ← {t("backComputers")}
      </Link>
      {state.loading ? (
        <Loading />
      ) : state.error ? (
        <ErrorBox error={state.error} retry={reload} />
      ) : (
        computer && (
          <>
            <Heading
              index={`02 / POWER CELLS / ${t("computer")}`}
              title={computer.name || computer.id.slice(0, 8)}
              action={
                <button
                  className="button secondary compact"
                  aria-label={t("refresh")}
                  onClick={reload}
                >
                  <Icon name="refresh" size={16} />
                  {t("refresh")}
                </button>
              }
            >
              {computer.os} · {computer.architecture} · {computer.runtime}
            </Heading>
            <section
              className="panel computer-details"
              aria-labelledby="computer-details-title"
            >
              <h2 id="computer-details-title">{t("computerDetails")}</h2>
              <dl>
                <div>
                  <dt>{t("computerId")}</dt>
                  <dd>{computer.id}</dd>
                </div>
                <div>
                  <dt>{t("status")}</dt>
                  <dd>
                    {!computer.enabled
                      ? t("disabled")
                      : computer.online
                        ? t("online")
                        : t("offline")}
                  </dd>
                </div>
                <div>
                  <dt>
                    CPU · {t("free")} / {t("total")}
                  </dt>
                  <dd>
                    {number(
                      Math.max(0, computer.cpu_millis - computer.reserved_cpu) /
                        1000,
                      locale,
                    )}{" "}
                    / {number(computer.cpu_millis / 1000, locale)}
                  </dd>
                </div>
                <div>
                  <dt>
                    {t("memory")} · {t("free")} / {t("total")}
                  </dt>
                  <dd>
                    {number(
                      Math.max(
                        0,
                        computer.memory_bytes - computer.reserved_memory,
                      ) / GiB,
                      locale,
                    )}{" "}
                    / {number(computer.memory_bytes / GiB, locale)} GiB
                  </dd>
                </div>
              </dl>
            </section>
            <Benchmarks key={computerId} computerId={computerId} interrupted={interrupted} />
          </>
        )
      )}
    </>
  );
}
