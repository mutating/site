import { useI18n } from "../lib/i18n";
import { ApiImage } from "./ApiImage";
import { Icon } from "./UI";

export function OrganizationRowContent({
  name,
  subtitle,
  role,
  avatarPath,
  memberCount,
  computerCount,
  invited = false,
}: {
  name: string;
  subtitle: string;
  role: string;
  avatarPath?: string | null;
  memberCount?: number;
  computerCount: number | null;
  invited?: boolean;
}) {
  const { t, locale } = useI18n();
  const initials = name
    .trim()
    .split(/\s+/u)
    .slice(0, 2)
    .map((part) => Array.from(part)[0])
    .join("")
    .toLocaleUpperCase(locale);
  const computers = computerCount?.toLocaleString(locale) ?? "—";
  return (
    <>
      <span className="organization-monogram" aria-hidden="true">
        {avatarPath ? <ApiImage path={avatarPath} fallback={initials} /> : initials}
      </span>
      <div className="organization-info">
        <h3>{name}</h3>
        <small>{subtitle}</small>
      </div>
      <span className={`badge organization-role${invited ? " invitation-badge" : ""}`}>
        {role}
      </span>
      {memberCount !== undefined && (
        <span
          className="organization-metric organization-members-metric"
          role="img"
          aria-label={`${t("members")}: ${memberCount.toLocaleString(locale)}`}
        >
          <Icon name="users" size={16} />
          <span>{memberCount.toLocaleString(locale)}</span>
        </span>
      )}
      <span
        className="organization-metric organization-computers-metric"
        role="img"
        aria-label={`${t("computers")}: ${computers}`}
      >
        <Icon name="computer" size={16} />
        <span data-testid="organization-computer-count">{computers}</span>
      </span>
    </>
  );
}
