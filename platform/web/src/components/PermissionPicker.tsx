import { useI18n } from "../lib/i18n";
import {
  permissionLabels,
  permissions,
  rolePermissions,
} from "../lib/permissions";
import type { Permission, Role } from "../lib/types";

export function PermissionPicker({
  role,
  onRole,
  custom,
  onCustom,
  selected,
  onSelected,
  canGrant,
  privileged,
  disabled = false,
}: {
  role: Role;
  onRole: (value: Role) => void;
  custom: boolean;
  onCustom: (value: boolean) => void;
  selected: Permission[];
  onSelected: (value: Permission[]) => void;
  canGrant: Permission[];
  privileged: boolean;
  disabled?: boolean;
}) {
  const { t } = useI18n();
  const shown =
    role === "creator"
      ? permissions
      : custom
        ? selected
        : rolePermissions(role);
  return (
    <div className="permission-picker">
      <label className="field">
        {t("memberRole")}
        <select
          aria-label={t("memberRole")}
          value={role}
          disabled={disabled}
          onChange={(event) => onRole(event.target.value as Role)}
        >
          {(privileged
            ? (["user", "admin", "creator"] as const)
            : (["user"] as const)
          ).map((value) => (
            <option key={value} value={value}>
              {t(value)}
            </option>
          ))}
        </select>
      </label>
      {role === "creator" ? (
        <p className="field-help">{t("creatorPermissions")}</p>
      ) : (
        <label className="check-field">
          <input
            type="checkbox"
            checked={custom}
            disabled={disabled}
            onChange={(event) => onCustom(event.target.checked)}
          />
          {t("customPermissions")}
        </label>
      )}
      {custom && role !== "creator" ? (
        <fieldset className="permission-grid" disabled={disabled}>
          <legend>{t("memberPermissions")}</legend>
          {permissions.map((permission) => (
            <label className="check-field" key={permission}>
              <input
                type="checkbox"
                checked={selected.includes(permission)}
                disabled={
                  !canGrant.includes(permission) &&
                  !selected.includes(permission)
                }
                onChange={(event) =>
                  onSelected(
                    event.target.checked
                      ? [...selected, permission]
                      : selected.filter((p) => p !== permission),
                  )
                }
              />
              {t(permissionLabels[permission])}
            </label>
          ))}
        </fieldset>
      ) : (
        <ul className="permission-summary">
          {shown.map((permission) => (
            <li key={permission}>{t(permissionLabels[permission])}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
