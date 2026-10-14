import { useEffect, useState } from "react";
import { api, ApiError, json } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { memberName, rolePermissions } from "../lib/permissions";
import type {
  Company,
  CreatedInvitation,
  MemberProfile,
  Permission,
  Role,
} from "../lib/types";
import { ErrorBox } from "./UI";
import { PermissionPicker } from "./PermissionPicker";
import { ShareLink } from "./InvitationUI";

export function MemberSharing({
  company,
  onChanged,
  linkRevision = 0,
}: {
  company: Company;
  onChanged: () => void;
  linkRevision?: number;
}) {
  const { t } = useI18n();
  const [mode, setMode] = useState<"add" | "invite" | "link">("add");
  const [identifier, setIdentifier] = useState("");
  const [found, setFound] = useState<MemberProfile | null>(null);
  const [role, setRole] = useState<Role>("user");
  const [custom, setCustom] = useState(
    !rolePermissions("user").every((permission) =>
      company.my_permissions.includes(permission),
    ),
  );
  const [selected, setSelected] = useState<Permission[]>(
    rolePermissions("user").filter((p) => company.my_permissions.includes(p)),
  );
  const [days, setDays] = useState(7);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [lookupMissing, setLookupMissing] = useState(false);
  const [success, setSuccess] = useState<
    "memberAdded" | "invitationCreated" | null
  >(null);
  const [link, setLink] = useState<string | null>(null);
  useEffect(() => {
    setLink(null);
  }, [linkRevision]);
  return (
    <section className="panel member-sharing" aria-label={t("addMember")}>
      <details>
        <summary>{t("addMember")}</summary>
        <form
          onSubmit={async (event) => {
            event.preventDefault();
            if (busy || (mode === "add" && !found)) return;
            setBusy(true);
            setError(null);
            setSuccess(null);
            setLink(null);
            setLookupMissing(false);
            const grant = {
              company_id: company.id,
              role,
              permissions: role === "creator" || !custom ? null : selected,
            };
            try {
              if (mode === "add")
                await api(
                  "memberships",
                  json("POST", { ...grant, user_id: found!.id }),
                );
              else {
                const result = await api<CreatedInvitation>(
                  "invitations",
                  json("POST", {
                    ...grant,
                    ...(mode === "invite"
                      ? { identifier: identifier.trim() }
                      : {}),
                    expires_in_seconds: days * 86400,
                  }),
                );
                setLink(result.token);
              }
              setSuccess(mode === "add" ? "memberAdded" : "invitationCreated");
              setIdentifier("");
              setFound(null);
              onChanged();
            } catch (e) {
              setError(e);
            } finally {
              setBusy(false);
            }
          }}
        >
          <label className="field">
            {t("sharingMethod")}
            <select
              aria-label={t("sharingMethod")}
              value={mode}
              disabled={busy}
              onChange={(event) => {
                setMode(event.target.value as typeof mode);
                setFound(null);
                setError(null);
                setSuccess(null);
                setLookupMissing(false);
                setLink(null);
              }}
            >
              <option value="add">{t("directMember")}</option>
              <option value="invite">{t("addressedInvite")}</option>
              <option value="link">{t("openInvite")}</option>
            </select>
          </label>
          {mode !== "link" && (
            <label className="field">
              {t("identifier")}
              <input
                value={identifier}
                disabled={busy}
                required
                maxLength={320}
                autoComplete="off"
                onChange={(event) => {
                  setIdentifier(event.target.value);
                  setFound(null);
                  setLookupMissing(false);
                }}
              />
            </label>
          )}
          {mode === "add" && (
            <button
              className="button secondary"
              type="button"
              disabled={busy || !identifier.trim()}
              onClick={async () => {
                if (busy) return;
                setBusy(true);
                setError(null);
                setFound(null);
                setLookupMissing(false);
                setSuccess(null);
                try {
                  setFound(
                    await api<MemberProfile>(
                      `companies/${company.id}/users/lookup?identifier=${encodeURIComponent(identifier.trim())}`,
                    ),
                  );
                } catch (e) {
                  if (e instanceof ApiError && e.status === 404)
                    setLookupMissing(true);
                  else setError(e);
                } finally {
                  setBusy(false);
                }
              }}
            >
              {t(busy ? "waiting" : "findUser")}
            </button>
          )}
          {lookupMissing && (
            <div className="error-box" role="alert">
              {t("userNotFound")}
            </div>
          )}
          {found && (
            <p className="found-member" role="status">
              <strong>{memberName(found)}</strong>
              {found.nickname && <span>@{found.nickname}</span>}
            </p>
          )}
          {mode === "link" && (
            <p className="field-help">{t("openInviteHelp")}</p>
          )}
          <PermissionPicker
            role={role}
            onRole={(value) => {
              setRole(value);
              setCustom(false);
              setSelected(rolePermissions(value));
            }}
            custom={custom}
            onCustom={setCustom}
            selected={selected}
            onSelected={setSelected}
            canGrant={company.my_permissions}
            privileged={company.my_role === "creator"}
            disabled={busy}
          />
          {mode !== "add" && (
            <label className="field">
              {t("invitationDays")}
              <select
                aria-label={t("invitationDays")}
                value={days}
                disabled={busy}
                onChange={(event) => setDays(Number(event.target.value))}
              >
                <option value={1}>{t("oneDay")}</option>
                <option value={7}>{t("sevenDaysInvite")}</option>
                <option value={30}>{t("thirtyDays")}</option>
              </select>
            </label>
          )}
          <ErrorBox
            error={error}
            conflict={mode === "add" ? "memberConflict" : "invitationConflict"}
          />
          {success && (
            <p className="success-message" role="status">
              {t(success)}
            </p>
          )}
          <button
            className="button primary"
            disabled={
              busy ||
              (mode === "add"
                ? !found
                : mode === "invite"
                  ? !identifier.trim()
                  : false)
            }
          >
            {t(
              busy
                ? "waiting"
                : mode === "add"
                  ? "addMember"
                  : mode === "invite"
                    ? "inviteMember"
                    : "createInviteLink",
            )}
          </button>
        </form>
        {link && <ShareLink key={link} token={link} />}
      </details>
    </section>
  );
}
