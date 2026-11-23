import { useState } from "react";
import { api, ApiError, json } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { memberName, rolePermissions } from "../lib/permissions";
import type {
  Company,
  CreatedInvitation,
  Invitation,
  InvitationLinkCheck,
  MemberProfile,
  Permission,
  Role,
} from "../lib/types";
import { ErrorBox, Icon } from "./UI";
import { Select } from "./Select";
import { PermissionPicker } from "./PermissionPicker";
import { invitationLinkIsOutdated, ShareLink } from "./InvitationUI";

type GrantDraft = { role: Role; custom: boolean; selected: Permission[] };

export function MemberSharing({
  company,
  onChanged,
  onAccessError,
  invitations,
  accessRevision,
  linkRevision,
  showInvitationLinks,
  verifyInvitationsFrom,
  invitationChecks,
  restoreInvitationFocus,
}: {
  company: Company;
  onChanged: (addedMember: boolean) => void;
  onAccessError: () => void;
  invitations: Invitation[] | null;
  accessRevision: number;
  linkRevision: number;
  showInvitationLinks: boolean;
  verifyInvitationsFrom: number;
  invitationChecks: ReadonlyMap<string, InvitationLinkCheck>;
  restoreInvitationFocus: () => void;
}) {
  const { t } = useI18n();
  const [mode, setMode] = useState<"add" | "invite" | "link">("add");
  const [identifier, setIdentifier] = useState("");
  const [found, setFound] = useState<MemberProfile | null>(null);
  const [draft, setDraft] = useState<GrantDraft>(() => ({
    role: "user",
    custom: false,
    selected: rolePermissions("user"),
  }));
  const [days, setDays] = useState(7);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [lookupMissing, setLookupMissing] = useState(false);
  const [success, setSuccess] = useState<
    "memberAdded" | "invitationCreated" | null
  >(null);
  const [link, setLink] = useState<{ invitation: CreatedInvitation; accessRevision: number; verifiedFrom: number } | null>(null);
  const [previousLinkRevision, setPreviousLinkRevision] = useState(linkRevision);
  const privileged = company.my_role === "creator";
  const canGrant = company.my_permissions;
  const role = privileged ? draft.role : "user";
  const defaults = rolePermissions(role);
  const defaultsAllowed = defaults.every((permission) => canGrant.includes(permission));
  const roleChanged = role !== draft.role;
  const custom = !defaultsAllowed || draft.custom;
  const resetSelection = !draft.custom && (roleChanged || !defaultsAllowed);
  const selected = (resetSelection ? defaults : draft.selected)
    .filter((permission) => canGrant.includes(permission));
  // Live access can change while the form stays mounted. Reconcile the draft
  // so the picker and the submitted grant use the same role and permissions.
  if (roleChanged || resetSelection || selected.length !== draft.selected.length) {
    setDraft({ role, custom, selected });
  }
  if (previousLinkRevision !== linkRevision) {
    setPreviousLinkRevision(linkRevision);
    setLink(null);
  }
  if (link && (link.accessRevision !== accessRevision ||
      invitationLinkIsOutdated(link.invitation, invitations, invitationChecks.get(link.invitation.id)))) {
    setLink(null);
    setSuccess(null);
  }
  return (
    <section className="panel member-sharing" aria-label={t("addMember")}>
      <details>
        <summary><Icon name="plus" size={16} />{t("addMember")}<span className="organization-team-chevron"><Icon name="chevron-down" size={15} /></span></summary>
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
                setLink({ invitation: result, accessRevision, verifiedFrom: verifyInvitationsFrom });
              }
              setSuccess(mode === "add" ? "memberAdded" : "invitationCreated");
              setIdentifier("");
              setFound(null);
              onChanged(mode === "add");
            } catch (e) {
              setError(e);
              if (e instanceof ApiError && (e.status === 403 || e.status === 404)) onAccessError();
            } finally {
              setBusy(false);
            }
          }}
        >
          <label className="field">
            {t("sharingMethod")}
            <Select
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
            </Select>
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
                  if (e instanceof ApiError && (e.status === 403 || e.status === 404)) onAccessError();
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
              setDraft({ role: value, custom: false, selected: rolePermissions(value) });
            }}
            custom={custom}
            onCustom={(value) => setDraft((previous) => ({ ...previous, custom: value }))}
            selected={selected}
            onSelected={(value) => setDraft((previous) => ({ ...previous, selected: value }))}
            canGrant={canGrant}
            privileged={privileged}
            defaultsAllowed={defaultsAllowed}
            disabled={busy}
          />
          {mode !== "add" && (
            <label className="field">
              {t("invitationDays")}
              <Select
                aria-label={t("invitationDays")}
                value={days}
                disabled={busy}
                onChange={(event) => setDays(Number(event.target.value))}
              >
                <option value={1}>{t("oneDay")}</option>
                <option value={7}>{t("sevenDaysInvite")}</option>
                <option value={30}>{t("thirtyDays")}</option>
              </Select>
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
        {link && (showInvitationLinks || link.verifiedFrom === verifyInvitationsFrom) &&
          <ShareLink key={link.invitation.token} token={link.invitation.token} restoreFocus={restoreInvitationFocus} />}
      </details>
    </section>
  );
}
