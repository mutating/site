import { useEffect, useId, useRef, useState } from "react";
import { api, ApiError, json } from "../lib/api";
import { useI18n } from "../lib/i18n";
import { organizationImagePath, type OrganizationImage } from "../lib/organizationImages";
import type { Company, CompanyAppearance } from "../lib/types";
import { ApiTokens } from "../pages/ApiTokens";
import { ApiImage } from "./ApiImage";
import { SectionTabs } from "./SectionTabs";
import { ErrorBox, Icon } from "./UI";

type Section = "general" | "tokens";
type Field = "name" | OrganizationImage;

export function OrganizationSettings({ company, check, onClose, onLeave, onDelete, onRenamed, onAppearanceChanged, onCheckPermissions }: {
  company: Company;
  check?: { error: unknown; retry: () => void; scoped?: boolean };
  onClose: () => void;
  onLeave: () => void;
  onDelete: () => void;
  onRenamed: (name: string) => void;
  onAppearanceChanged: (appearance: CompanyAppearance) => void;
  onCheckPermissions: (error?: ApiError) => void;
}) {
  const { t } = useI18n();
  const id = useId();
  const ref = useRef<HTMLDialogElement>(null);
  const [section, setSection] = useState<Section>("general");
  const [draft, setDraft] = useState<string | null>(null);
  const [pending, setPending] = useState<Field | null>(null);
  const operation = useRef<Field | null>(null);
  const imageInputs = useRef<Partial<Record<OrganizationImage, HTMLInputElement | null>>>({});
  const [result, setResult] = useState<{ field: Field; error?: unknown } | null>(null);
  const canManage = company.my_permissions.includes("company.manage");
  const canUseTokens = company.my_permissions.some((permission) => permission === "tokens.read" || permission === "tokens.manage");
  const active = canUseTokens ? section : "general";
  const sections: { key: Section; label: string }[] = [{ key: "general", label: t("organizationGeneral") }];
  if (canUseTokens) sections.push({ key: "tokens", label: t("apiTokens") });
  const disabled = !!check || pending !== null;
  const name = draft ?? company.name;

  useEffect(() => {
    const dialog = ref.current!;
    const trigger = document.activeElement;
    dialog.showModal();
    return () => {
      dialog.close();
      requestAnimationFrame(() => {
        if (trigger instanceof HTMLElement && trigger.isConnected) trigger.focus({ preventScroll: true });
      });
    };
  }, []);
  useEffect(() => {
    if (canUseTokens || section !== "tokens") return;
    setSection("general");
    document.getElementById(`${id}-tab-general`)?.focus({ preventScroll: true });
  }, [canUseTokens, id, section]);

  async function save(field: Field, update: () => Promise<void>) {
    if (!canManage || check || operation.current) return;
    operation.current = field;
    setPending(field);
    setResult(null);
    try {
      await update();
      setResult({ field });
    } catch (error) {
      setResult({ field, error });
      if (error instanceof ApiError && [403, 404].includes(error.status)) onCheckPermissions();
    } finally {
      operation.current = null;
      setPending(null);
    }
  }

  function changeImage(kind: OrganizationImage, file?: File) {
    void save(kind, async () => {
      if (file && file.size > 2 * 1024 * 1024) throw new ApiError(413, "");
      const trigger = document.activeElement;
      const appearance = await api<CompanyAppearance>(`companies/${company.id}/images/${kind}`, file ? {
        method: "PUT",
        body: file,
        headers: { "Content-Type": file.type || "application/octet-stream" },
      } : { method: "DELETE" });
      onAppearanceChanged(appearance);
      if (!file && trigger instanceof HTMLButtonElement) {
        requestAnimationFrame(() => {
          if (!trigger.isConnected && document.activeElement === document.body) {
            imageInputs.current[kind]?.focus({ preventScroll: true });
          }
        });
      }
    });
  }

  function feedback(field: Field) {
    if (result?.field !== field) return null;
    return result.error ? <ErrorBox error={result.error} /> : <p className="success-message" role="status">{t("saved")}</p>;
  }

  return (
    <dialog ref={ref} className="confirm-dialog organization-settings-dialog" aria-labelledby={`${id}-title`}
      onCancel={(event) => {
        if (event.target !== event.currentTarget) return;
        event.preventDefault();
        if (!operation.current) onClose();
      }}>
      <header className="organization-settings-header">
        <h2 id={`${id}-title`}>{t("organizationSettings")}</h2>
        <button className="button secondary compact" type="button" aria-label={t("closeDialog")} onClick={onClose} disabled={pending !== null}>
          <Icon name="close" size={18} />
        </button>
      </header>
      <SectionTabs id={id} label={t("organizationSettings")} options={sections} active={active} onChange={setSection} />
      <div id={`${id}-panel-general`} role="tabpanel" aria-labelledby={`${id}-tab-general`} hidden={active !== "general"}>
        {check && <ErrorBox error={check.error} retry={check.retry} />}
        {!canManage && <ErrorBox error={result?.error} />}
        {canManage && (
          <>
            <form className="organization-name-form" onSubmit={(event) => {
              event.preventDefault();
              const next = name.trim();
              if (!next || next === company.name) return;
              void save("name", async () => {
                const updated = await api<Pick<Company, "name">>(`companies/${company.id}`, json("PATCH", { name: next }));
                onRenamed(updated.name);
                setDraft(null);
              });
            }}>
              <label className="field">
                {t("organizationName")}
                <input name="name" value={name} onChange={(event) => {
                  const value = event.target.value;
                  setDraft(value === company.name ? null : value);
                  if (result?.field === "name") setResult(null);
                }} maxLength={200} readOnly={disabled} required />
              </label>
              <button className="button primary" aria-disabled={disabled || !name.trim() || name.trim() === company.name}>
                {t(pending === "name" ? "waiting" : "saveShort")}
              </button>
              {feedback("name")}
            </form>
            <div className="organization-image-settings">
              {(["background", "avatar"] as const).map((kind) => {
                const background = kind === "background";
                const path = organizationImagePath(company, kind);
                const upload = t(background ? "uploadBackground" : "uploadAvatar");
                return <section key={kind} className="organization-image-setting" aria-label={t(background ? "organizationBackground" : "organizationAvatar")}>
                  <span className={`organization-image-preview ${kind}`} aria-hidden="true">
                    <ApiImage path={path} fallback={<Icon name={background ? "camera" : "building"} size={23} />} />
                  </span>
                  <div className="organization-image-controls">
                    <h3>{t(background ? "organizationBackground" : "organizationAvatar")}</h3>
                    <div className="form-actions">
                      <label className={`button secondary compact file-button${disabled ? " is-disabled" : ""}`}>
                        {pending === kind ? t("waiting") : upload}
                        <input type="file" ref={(input) => { imageInputs.current[kind] = input; }} aria-label={upload}
                          accept="image/png,image/jpeg,image/webp" aria-disabled={disabled || undefined}
                          onClick={(event) => { if (disabled) event.preventDefault(); }} onChange={(event) => {
                          const file = event.target.files?.[0];
                          event.target.value = "";
                          if (file) changeImage(kind, file);
                        }} />
                      </label>
                      {path && <button className="text-button" type="button" aria-disabled={disabled || undefined} onClick={() => changeImage(kind)}>
                        {t(background ? "removeBackground" : "removeAvatar")}
                      </button>}
                    </div>
                    {feedback(kind)}
                  </div>
                </section>;
              })}
              <p className="field-help">{t("avatarHelp")}</p>
            </div>
          </>
        )}
        <div className="organization-danger-actions">
          {company.my_membership_id && <button type="button" className="button secondary danger-text" aria-disabled={disabled || undefined}
            onClick={(event) => {
              if (disabled) return;
              event.currentTarget.focus({ preventScroll: true });
              onLeave();
            }}><Icon name="exit" size={17} />{t("leave")}</button>}
          {company.my_role === "creator" && <button type="button" className="button secondary danger-text" aria-disabled={disabled || undefined}
            onClick={(event) => {
              if (disabled) return;
              event.currentTarget.focus({ preventScroll: true });
              onDelete();
            }}><Icon name="trash" size={17} />{t("deleteOrganization")}</button>}
        </div>
      </div>
      {canUseTokens && <div id={`${id}-panel-tokens`} role="tabpanel" aria-labelledby={`${id}-tab-tokens`} hidden={active !== "tokens"}>
        {active === "tokens" && <ApiTokens company={company} check={check} onCheckPermissions={onCheckPermissions} />}
      </div>}
    </dialog>
  );
}
