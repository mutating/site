import { useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { Dropdown, SquareButton } from "@mutating/ui/react";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import { Avatar } from "./Avatar";
import { ErrorBox } from "./UI";

export function UserMenu() {
  const { user, signOut, signingOut } = useSession();
  const { t } = useI18n();
  const location = useLocation();
  const [error, setError] = useState<unknown>(null);
  if (!user) return null;
  return <Dropdown key={location.key} label={t("userMenu")} className="user-menu"
    trigger={attributes => <SquareButton {...attributes} aria-label={t("userMenu")} title={user.email}>
      <span aria-hidden="true"><Avatar round /></span>
    </SquareButton>}>
    <Link role="menuitem" to="/profile" aria-current={location.pathname === "/profile" ? "page" : undefined}>{t("profile")}</Link>
    <button role="menuitem" type="button" disabled={signingOut} aria-busy={signingOut} onClick={async () => {
      setError(null);
      try { await signOut(); } catch (error) { setError(error); }
    }}>{t(signingOut ? "waiting" : "logout")}</button>
    <ErrorBox error={error} />
  </Dropdown>;
}
