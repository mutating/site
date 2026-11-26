import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Dropdown, SquareButton } from "@mutating/ui/react";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import { userMenuIdentity } from "../lib/userIdentity";
import { Avatar } from "./Avatar";
import { ErrorBox } from "./UI";

export function UserMenu() {
  const { user, signOut, signingOut } = useSession();
  const { t } = useI18n();
  const location = useLocation();
  const [error, setError] = useState<unknown>(null);
  if (!user) return null;
  const identity = userMenuIdentity(user);
  const displayName = identity.initial ? `${identity.name} ${identity.initial}` : identity.name;
  return <Dropdown key={location.key} label={t("userMenu")} className="user-menu"
    header={
      <Link role="menuitem" to="/profile" className="user-menu-header"
        aria-label={`${displayName} — ${t("profile")}`}
        aria-current={location.pathname === "/profile" ? "page" : undefined}>
        <span className="user-menu-header__name">
          <span className="user-menu-header__first-name">{identity.name}</span>
          {identity.initial && <>{" "}<span className="user-menu-header__initial">{identity.initial}</span></>}
        </span>
      </Link>
    }
    trigger={attributes => <SquareButton {...attributes} aria-label={t("userMenu")} title={user.email}>
      <span aria-hidden="true"><Avatar round /></span>
    </SquareButton>}>
    <button role="menuitem" type="button" disabled={signingOut} aria-busy={signingOut} onClick={async () => {
      setError(null);
      try { await signOut(); } catch (error) { setError(error); }
    }}>{t(signingOut ? "waiting" : "logout")}</button>
    <ErrorBox error={error} />
  </Dropdown>;
}
