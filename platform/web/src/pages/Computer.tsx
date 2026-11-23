import { useRef, type RefObject } from "react";
import { Link, useLocation, useParams, useSearchParams } from "react-router-dom";
import { ApiError, request, withDeadline } from "../lib/api";
import { useReloadOnComputerPoolChange } from "../lib/compute";
import { computerAvailability } from "../lib/computerAvailability";
import { useData, useRefreshWhileVisible } from "../lib/useData";
import { useReloadOnOrganizationChange } from "../lib/useOrganizationDirectory";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import { GiB, number } from "../lib/chart";
import type { Computer as ComputerData } from "../lib/types";
import { Benchmarks } from "../components/Benchmarks";
import { ErrorBox, Heading, Icon, Loading } from "../components/UI";
import { runtimeLabel } from "../lib/runtime";

export function Computer({ interrupted }: { interrupted: RefObject<boolean> }) {
  const computerId = useParams().computerId!.toLowerCase();
  return <ComputerView key={computerId} computerId={computerId} interrupted={interrupted} />;
}

function ComputerView({ computerId, interrupted }: {
  computerId: string;
  interrupted: RefObject<boolean>;
}) {
  const { t, locale } = useI18n();
  const viewer = useSession().user!.id;
  const connectedPoolOrigin = useLocation().state?.connectedPoolOrigin;
  const [params] = useSearchParams();
  const companyId = params.get("company_id")?.toLowerCase() ?? null;
  const back = new URLSearchParams(params);
  back.delete("benchmark");
  if (!back.has("company_id") && !back.has("user_id")) back.set("user_id", viewer);
  const backLink = useRef<HTMLAnchorElement>(null);
  const details = useRef<HTMLDivElement>(null);
  const retry = useRef<HTMLButtonElement>(null);
  const verifiedOwner = useRef<{ companyId: string | null; membershipId?: string; revision?: number } | null>(null);
  const clearDetails = () => {
    if (details.current?.contains(document.activeElement)) backLink.current?.focus();
    state.update(null);
  };
  const state = useData<ComputerData>(
    async (signal) => {
      const read = (detailSignal: AbortSignal) => request<ComputerData>(
        `computers/${encodeURIComponent(computerId)}`, { signal: detailSignal },
      );
      try {
        const result = await (companyId ? withDeadline(signal, read) : read(signal));
        if (!signal.aborted) {
          verifiedOwner.current = {
            companyId: result.organizationId ?? (result.organizationMembershipId ? companyId : null),
            membershipId: result.organizationMembershipId,
            revision: result.poolCheckRevision ?? verifiedOwner.current?.revision,
          };
          if (document.activeElement === retry.current) backLink.current?.focus();
        }
        return result.data;
      } catch (error) {
        if (!signal.aborted && error instanceof ApiError && [401, 403, 404].includes(error.status)) {
          verifiedOwner.current = error.organizationMembershipId
            ? { companyId: error.organizationId ?? companyId, membershipId: error.organizationMembershipId,
                revision: error.poolCheckRevision ?? verifiedOwner.current?.revision }
            : null;
          clearDetails();
        }
        throw error;
      }
    },
    computerId,
    true,
    { retainError: true },
  );
  useReloadOnOrganizationChange(viewer, "all", (change) => {
    if (change.type === "created") return;
    const owner = verifiedOwner.current;
    const currentCompany = change.companyId === (owner ? owner.companyId : companyId);
    if (change.type === "removed") {
      const applies = change.membershipId ? change.membershipId === owner?.membershipId : currentCompany;
      if (applies) clearDetails();
      if (applies || currentCompany) state.reload();
    } else if (currentCompany && change.type !== "renamed") {
      clearDetails();
      state.reload();
    }
  });
  useReloadOnComputerPoolChange(viewer, (change) => {
    if (change.computerId !== computerId) return;
    const revision = verifiedOwner.current?.revision;
    if (change.assignment && (revision === undefined || change.assignment.revision > revision)) {
      verifiedOwner.current = change.assignment;
      if (!change.assignment.canRead) clearDetails();
    }
    state.reload();
  });
  useRefreshWhileVisible(state.reload);
  const reload = () => {
    interrupted.current = false;
    state.update(null);
    state.reload();
  };
  const computer = state.data;
  const availability = computer && computerAvailability(computer);
  return (
    <>
      <Link
        ref={backLink}
        className="back-link"
        to={`/compute/computers?${back}`}
        state={connectedPoolOrigin ? { connectedPoolOrigin } : undefined}
      >
        ← {t("backComputers")}
      </Link>
      <ErrorBox error={state.error} retry={reload} retryRef={retry} />
      {state.loading && !computer && !state.error ? (
        <Loading />
      ) : (
        computer && availability && (
          <div ref={details} style={{ display: "contents" }}>
            <Heading
              index={`02 / MUTATING COMPUTE / ${t("computer")}`}
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
              {computer.os} · {computer.architecture} ·{" "}
              {runtimeLabel(computer.runtime)}
            </Heading>
            <section
              className="panel computer-details"
              aria-labelledby="computer-details-title"
              aria-busy={state.loading}
            >
              <h2 id="computer-details-title">{t("computerDetails")}</h2>
              <dl>
                <div>
                  <dt>{t("computerId")}</dt>
                  <dd>{computer.id}</dd>
                </div>
                <div>
                  <dt>{t("status")}</dt>
                  <dd>{t(availability.status)}</dd>
                </div>
                <div>
                  <dt>
                    CPU · {t("free")} / {t("total")}
                  </dt>
                  <dd>
                    {availability.available ? number(computer.free_cpu / 1000, locale) : "—"}{" "}
                    / {number(computer.cpu_millis / 1000, locale)}
                  </dd>
                </div>
                <div>
                  <dt>
                    {t("memory")} · {t("free")} / {t("total")}
                  </dt>
                  <dd>
                    {availability.available ? number(computer.free_memory / GiB, locale) : "—"}{" "}
                    / {number(computer.memory_bytes / GiB, locale)} GiB
                  </dd>
                </div>
              </dl>
            </section>
            <Benchmarks key={computerId} computerId={computerId} interrupted={interrupted} />
          </div>
        )
      )}
    </>
  );
}
