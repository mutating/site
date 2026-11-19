import { useEffect, useRef, useState } from "react";
import { api, ApiError, json } from "../lib/api";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import { memberName, rolePermissions } from "../lib/permissions";
import type { Company, Membership, Permission, Role } from "../lib/types";
import { PermissionPicker } from "./PermissionPicker";
import { ErrorBox } from "./UI";

export function MemberEditor({
  member,
  company,
  onClose,
  onSaved,
  onAccessError,
  restoreFocus,
}: {
  member: Membership;
  company: Company;
  onClose: () => void;
  onSaved: (member: Membership) => void;
  onAccessError: (status: 403 | 404) => void;
  restoreFocus: () => void;
}) {
  const { t } = useI18n();
  const { user } = useSession();
  const ref = useRef<HTMLDialogElement>(null);
  const [role, setRole] = useState<Role>(member.role);
  const [custom, setCustom] = useState(member.permissions !== null);
  const [selected, setSelected] = useState<Permission[]>(
    member.effective_permissions,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    const dialog = ref.current,
      trigger = document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      requestAnimationFrame(() => {
        if (document.activeElement !== document.body && document.activeElement !== trigger) return;
        if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus({ preventScroll: true });
        else restoreFocus();
      });
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="confirm-dialog member-editor"
      aria-labelledby="member-edit-title"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (busy) return;
          setBusy(true);
          setError(null);
          try {
            const saved = await api<Membership>(
              `memberships/${member.id}`,
              json("PATCH", {
                role,
                permissions: role === "creator" || !custom ? null : selected,
              }),
            );
            onSaved(saved);
            onClose();
          } catch (e) {
            if (e instanceof ApiError && (e.status === 403 || e.status === 404)) {
              onAccessError(e.status);
              if (e.status === 404) {
                onClose();
                return;
              }
            }
            setError(e);
            setBusy(false);
          }
        }}
      >
        <p className="eyebrow">{company.name}</p>
        <h2 id="member-edit-title">{t("editMember")}</h2>
        <p>{memberName(member.user)}</p>
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
        {member.user_id === user!.id && (
          <p className="field-help">{t("selfPermissionChange")}</p>
        )}
        <ErrorBox error={error} conflict="memberConflict" />
        <div className="form-actions">
          <button
            className="button secondary"
            type="button"
            disabled={busy}
            onClick={onClose}
          >
            {t("cancel")}
          </button>
          <button className="button primary" disabled={busy}>
            {t(busy ? "waiting" : "save")}
          </button>
        </div>
      </form>
    </dialog>
  );
}
