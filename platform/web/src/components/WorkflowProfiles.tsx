import { useEffect, useRef, useState } from "react";
import { useI18n, type MessageKey } from "../lib/i18n";
import type { CIProfile } from "../lib/github";
import type { Computer } from "../lib/types";

type NumericField = Exclude<keyof CIProfile, "computer_ids">;
type NumericSetting = { key: NumericField; label: MessageKey; unit: number; min: number; max: number; step: string };
const fields: NumericSetting[] = [
  { key: "cpu_millis", label: "bindingCPU", unit: 1000, min: 0.001, max: 1000, step: "0.001" },
  { key: "memory_bytes", label: "bindingRAM", unit: 1024 ** 3, min: 0.015625, max: 1048576, step: "any" },
  { key: "disk_bytes", label: "ciDisk", unit: 1024 ** 3, min: 1, max: 1024, step: "any" },
  { key: "preparation_seconds", label: "bindingPreparation", unit: 1, min: 10, max: 300, step: "1" },
  { key: "timeout_seconds", label: "bindingDuration", unit: 60, min: 1, max: 360, step: "any" },
];

function NumericOverride({ field, path, value, inherited, onChange }: { field: NumericSetting; path: string; value?: number; inherited: number; onChange: (value: number | undefined) => void }) {
  const { t } = useI18n();
  const [draft, setDraft] = useState(value === undefined ? "" : String(value / field.unit));
  return <label className="field">{t(field.label)}{field.key === "memory_bytes" || field.key === "disk_bytes" ? " · GiB" : ""}<input aria-label={`${t(field.label)} · ${path}`} type="number" min={field.min} max={field.max} step={field.step} value={draft} placeholder={String(inherited / field.unit)} onChange={event => { setDraft(event.target.value); onChange(event.target.value === "" ? undefined : Math.round(Number(event.target.value) * field.unit)); }} /><small>{t("bindingProfileInherited")}: {inherited / field.unit}</small></label>;
}

export function WorkflowProfiles({ profiles, onChange, defaults, workflows, computers, inventoryUnavailable, inventoryReadable }: {
  profiles: Record<string, Partial<CIProfile>>;
  onChange: (value: Record<string, Partial<CIProfile>>) => void;
  defaults: CIProfile;
  workflows: { path: string; name: string }[];
  computers: Computer[];
  inventoryUnavailable: boolean;
  inventoryReadable: boolean;
}) {
  const { t } = useI18n();
  const [chosen, setChosen] = useState("");
  const [pendingProfile, setPendingProfile] = useState<string | null>(null);
  const addedProfile = useRef<HTMLFieldSetElement>(null);
  useEffect(() => {
    if (!pendingProfile || !addedProfile.current) return;
    addedProfile.current.scrollIntoView({ block: "start" });
    addedProfile.current.querySelector<HTMLInputElement>('input[type="number"]')?.focus({ preventScroll: true });
    setPendingProfile(null);
  }, [pendingProfile, profiles]);
  const available = workflows.filter(row => !(row.path in profiles));
  const selected = available.some(row => row.path === chosen) ? chosen : available[0]?.path ?? "";
  const update = (path: string, field: keyof CIProfile, value: number | string[] | undefined) => {
    const next = { ...profiles[path], [field]: value };
    if (value === undefined) delete next[field];
    onChange({ ...profiles, [path]: next });
  };
  return <section className="workflow-profiles" aria-label={t("bindingProfiles")}>
    <h3>{t("bindingProfiles")}</h3><p className="field-help">{t("bindingProfilesHelp")}</p>
    {!inventoryReadable && <p role="status" className="field-help">{t("bindingComputersReadRestricted")}</p>}
    <div className="workflow-profile-add"><label className="field">{t("bindingProfileWorkflow")}<select aria-label={t("bindingProfileWorkflow")} value={selected} disabled={!available.length} onChange={event => setChosen(event.target.value)}>{!available.length && <option value="">{t("bindingProfileNoMore")}</option>}{available.map(row => <option key={row.path} value={row.path}>{row.name} · {row.path}</option>)}</select></label><button type="button" className="button secondary" disabled={!selected} onClick={() => { setPendingProfile(selected); onChange({ ...profiles, [selected]: {} }); }}>{t("bindingProfileAdd")}</button></div>
    {Object.entries(profiles).map(([path, profile]) => <fieldset className="workflow-profile" key={path} aria-label={path} ref={path === pendingProfile ? addedProfile : undefined}>
      <legend>{path}</legend><button type="button" className="text-button" onClick={() => { const next = { ...profiles }; delete next[path]; onChange(next); }}>{t("bindingProfileRemove")}</button>
      {!workflows.some(row => row.path === path) && <p className="field-help">{t("bindingProfileInactive")}</p>}
      <div className="binding-form-grid binding-resource-grid">{fields.slice(0, 3).map(field => <NumericOverride key={field.key} field={field} path={path} value={profile[field.key]} inherited={defaults[field.key]} onChange={value => update(path, field.key, value)} />)}</div>
      <div className="binding-form-grid">{fields.slice(3).map(field => <NumericOverride key={field.key} field={field} path={path} value={profile[field.key]} inherited={defaults[field.key]} onChange={value => update(path, field.key, value)} />)}</div>
      <label className="binding-check"><input type="checkbox" checked={profile.computer_ids !== undefined} onChange={event => update(path, "computer_ids", event.target.checked ? [] : undefined)} />{t("bindingProfileComputers")}</label>
      {profile.computer_ids !== undefined ? <><label className="field">{t("ciComputers")}<select multiple aria-label={`${t("ciComputers")} · ${path}`} value={profile.computer_ids} disabled={inventoryUnavailable || !computers.length} onChange={event => update(path, "computer_ids", Array.from(event.target.selectedOptions, row => row.value))}>{computers.map(row => <option key={row.id} value={row.id}>{row.name || row.id}</option>)}{profile.computer_ids.filter(id => !computers.some(row => row.id === id)).map(id => <option key={id} value={id} disabled>{id} · {t(inventoryUnavailable ? "bindingComputerPreserved" : "bindingComputerUnavailable")}</option>)}</select><small>{t("ciAnyComputer")}</small></label>{profile.computer_ids.length > 0 && <button type="button" className="text-button" onClick={() => update(path, "computer_ids", [])}>{t("ciClearComputers")}</button>}{!inventoryUnavailable && profile.computer_ids.some(id => !computers.some(row => row.id === id)) && <p role="status" className="field-help">{t("bindingComputersChanged")}</p>}</> : <p className="field-help">{t("bindingProfileInheritedComputers")}</p>}
    </fieldset>)}
  </section>;
}
