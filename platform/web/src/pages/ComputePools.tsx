import { useLayoutEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { OrganizationRowContent } from "../components/OrganizationRowContent";
import { PoolCard, RestrictedPoolCard } from "../components/PoolCard";
import { ErrorBox, Heading, Icon, Loading } from "../components/UI";
import { ApiError, request } from "../lib/api";
import { organizationPoolLabels, OutdatedPoolRead, poolPath, revealFocusedComputeControl, useComputeCompanies, usePoolRead, useReloadOnComputerPoolChange } from "../lib/compute";
import { useI18n } from "../lib/i18n";
import { organizationImagePath } from "../lib/organizationImages";
import { organizationEntries } from "../lib/organizations";
import { useOrganizationMembershipCheck, useReloadOnOrganizationChange } from "../lib/useOrganizationDirectory";
import { useSession } from "../lib/session";
import type { Company, Computer } from "../lib/types";
import { useData, useRefreshWhileVisible } from "../lib/useData";

export function ComputePools() {
  const userId = useSession().user!.id;
  const { t } = useI18n();
  const catalog = useRef<HTMLElement>(null);
  const personalLink = useRef<HTMLAnchorElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const companies = useComputeCompanies(userId, () => {
    const focused = document.activeElement;
    previouslyFocused.current = focused instanceof HTMLElement && catalog.current?.contains(focused) ? focused : null;
  });
  useLayoutEffect(() => {
    const previous = previouslyFocused.current;
    previouslyFocused.current = null;
    if (previous && !previous.isConnected && document.activeElement === document.body) {
      personalLink.current?.focus();
    }
  }, [companies.data]);
  const personal = useData(async (signal) => {
    const result = await request<Computer[]>(`computers?user_id=${encodeURIComponent(userId)}&limit=1`, { signal });
    return result.total ?? result.data.length;
  }, userId, true, { retainError: true });
  useRefreshWhileVisible(personal.reload);
  useReloadOnComputerPoolChange(userId, personal.reload, null);
  useReloadOnComputerPoolChange(userId, companies.reload);
  const availableCompanies = companies.data ?? [];
  const personalName = t("personalPool");
  const names = organizationPoolLabels(availableCompanies, personalName, t("organizationType"));
  const entries = organizationEntries(availableCompanies, [], userId);
  return (
    <>
      <Link className="back-link compute-back-link" to="/compute" onFocus={revealFocusedComputeControl}>← {t("backCompute")}</Link>
      <Heading title={t("computePools")}>{t("computePoolsLead")}</Heading>
      <ErrorBox error={personal.error} retry={personal.reload} />
      <ErrorBox error={companies.error} retry={companies.reload} />
      <section ref={catalog} className="organization-list compute-pool-list" aria-label={t("computePools")} aria-busy={companies.loading || personal.loading} onFocus={revealFocusedComputeControl}>
        <article className="organization-row" aria-label={personalName}>
          <Link ref={personalLink} className="organization-row-link" to={poolPath(null)} aria-label={`${t("openPool")} ${personalName}`}>
            <OrganizationRowContent
              name={personalName}
              subtitle={t("personalWorkspace")}
              role={t("personalPoolRole")}
              computerCount={personal.error ? null : personal.data}
            />
          </Link>
        </article>
        {entries.map((entry) => entry.kind === "company" && (
          <article className="organization-row" key={entry.id} aria-label={names.get(entry.id)}>
            <Link className="organization-row-link" to={poolPath(entry.id)} aria-label={`${t("openPool")} ${names.get(entry.id)}`}>
              <OrganizationRowContent
                name={entry.name}
                subtitle={t("sharedWorkspace")}
                role={t(entry.company.my_role)}
                avatarPath={organizationImagePath(entry.company, "avatar")}
                memberCount={entry.company.member_count}
                computerCount={companies.loading || companies.error ? null : entry.computerCount}
              />
            </Link>
          </article>
        ))}
      </section>
      {companies.loading && !companies.data && <Loading />}
    </>
  );
}

export function ComputePool() {
  const userId = useSession().user!.id;
  const companyId = useParams().poolId?.toLowerCase() ?? "";
  const { t } = useI18n();
  const [revision, setRevision] = useState(0);
  const key = `${userId}/${companyId}`;
  const { checked: membershipCheck, remember: recordMembership, resourcesRead,
    precedesMembershipCheck, precedesResourcePermissionCheck } = useOrganizationMembershipCheck(key);
  const acceptCompanyError = (error: ApiError) => error.status !== 404 || !precedesMembershipCheck(error.poolCheckRevision);
  const backToPools = useRef<HTMLAnchorElement>(null);
  const retry = useRef<HTMLButtonElement>(null);
  const card = useRef<HTMLElement>(null);
  const focusBackFromCard = () => {
    if (card.current?.contains(document.activeElement)) backToPools.current?.focus();
  };
  const applyResourceError = (error: ApiError) => {
    const { status, organizationMembershipId: id, poolCheckRevision } = error;
    if (!acceptCompanyError(error) || status === 403 && id &&
        (precedesMembershipCheck(poolCheckRevision, id) || precedesResourcePermissionCheck(id, poolCheckRevision))) return false;
    focusBackFromCard();
    return true;
  };
  const company = usePoolRead<{ key: string; info: Company; poolCheckRevision?: number }>(
    async (signal) => {
      try {
        const { data: info, poolCheckRevision } = await request<Company>(`companies/${encodeURIComponent(companyId)}`, { signal });
        if (!signal.aborted) {
          if (poolCheckRevision !== undefined && precedesMembershipCheck(poolCheckRevision, info.my_membership_id)) {
            throw new OutdatedPoolRead(poolCheckRevision);
          }
          const canRead = info.my_permissions.includes("resources.read");
          const accepted = recordMembership({ id: info.my_membership_id, revision: poolCheckRevision, resourcesRead: canRead });
          if (document.activeElement === retry.current) backToPools.current?.focus();
          else if (accepted && company.data?.info.my_permissions.includes("resources.read") && !canRead) focusBackFromCard();
        }
        return { key, info, poolCheckRevision };
      } catch (error) {
        if (!signal.aborted && error instanceof ApiError && [403, 404].includes(error.status) && acceptCompanyError(error)) {
          focusBackFromCard();
          company.update(null);
        }
        throw error;
      }
    },
    key,
    true,
    { revision, retainError: true, onAccessError: acceptCompanyError },
  );
  useRefreshWhileVisible(company.reload);
  useReloadOnComputerPoolChange(userId, ({ assignment }) => {
    if (assignment?.companyId === companyId && assignment.membershipId) {
      const accepted = recordMembership({ id: assignment.membershipId,
        revision: assignment.revision, resourcesRead: assignment.canRead });
      if (accepted && !assignment.canRead) focusBackFromCard();
    }
    company.reload();
  }, companyId);
  useReloadOnOrganizationChange(userId, companyId, (change) => {
    if (change.type === "removed") {
      const checked = membershipCheck.current;
      const currentMembershipId = checked?.key === key ? checked.id : company.data?.info.my_membership_id;
      if (!change.membershipId || currentMembershipId === change.membershipId) {
        focusBackFromCard();
        company.update(null);
      }
    }
    company.reload();
  }, { includeMemberChanges: true });
  const info = company.data?.key === key && company.data.info.id === companyId ? company.data.info : null;
  const pool = info && { ...info, kind: "companies" as const };
  return (
    <>
      <Link className="back-link compute-back-link" to="/compute" onFocus={revealFocusedComputeControl}>← {t("backCompute")}</Link>
      <Heading title={t("computePools")} action={
        <button className="button secondary compact" onClick={() => setRevision((value) => value + 1)}>
          <Icon name="refresh" size={16} /> {t("refresh")}
        </button>
      }>
        {t("resourcesNote")}
      </Heading>
      <Link ref={backToPools} className="back-link compute-back-link compute-pool-back" to="/compute/pools" onFocus={revealFocusedComputeControl}>← {t("backPools")}</Link>
      <div className="pool-list compute-pools" onFocus={revealFocusedComputeControl}>
        <ErrorBox error={company.error} retry={company.reload} retryRef={retry} />
        {info && !(resourcesRead ?? info.my_permissions.includes("resources.read")) ? <RestrictedPoolCard company={info} cardRef={card} />
          : pool ? <PoolCard key={`${pool.kind}/${pool.id}`} pool={pool} chart revision={revision} cardRef={card}
            grantedAt={pool.my_permissions.includes("resources.read") ? company.data?.poolCheckRevision : undefined}
            onAccessError={applyResourceError} onMembershipCheck={(member) => {
              if (precedesMembershipCheck(member.revision, member.id)) throw new OutdatedPoolRead(member.revision);
              recordMembership({ ...member, resourcesRead: true });
            }} />
          : !company.error && <Loading />}
      </div>
    </>
  );
}
