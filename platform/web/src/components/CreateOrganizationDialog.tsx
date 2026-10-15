import { useEffect, useRef, useState } from "react";
import { useI18n } from "../lib/i18n";
import { ErrorBox, Icon } from "./UI";

export function CreateOrganizationDialog({
  onCreate,
  onClose,
}: {
  onCreate: (name: string) => Promise<void>;
  onClose: () => void;
}) {
  const { t } = useI18n();
  const ref = useRef<HTMLDialogElement>(null);
  const inFlight = useRef(false);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => {
    const dialog = ref.current;
    const trigger = document.activeElement;
    dialog?.showModal();
    return () => {
      dialog?.close();
      if (trigger instanceof HTMLElement && trigger.isConnected)
        trigger.focus({ preventScroll: true });
    };
  }, []);
  return (
    <dialog
      ref={ref}
      className="confirm-dialog create-organization-dialog"
      aria-labelledby="create-organization-title"
      aria-describedby="create-organization-lead"
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
    >
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          if (!name.trim() || inFlight.current) return;
          inFlight.current = true;
          setBusy(true);
          setError(null);
          try {
            await onCreate(name.trim());
            onClose();
          } catch (err) {
            inFlight.current = false;
            setError(err);
            setBusy(false);
          }
        }}
      >
        <p className="eyebrow">{t("organizations")}</p>
        <h2 id="create-organization-title">{t("createOrganization")}</h2>
        <p className="muted" id="create-organization-lead">
          {t("createOrganizationLead")}
        </p>
        <label className="field">
          {t("organizationName")}
          <input
            autoFocus
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={200}
            required
            disabled={busy}
          />
        </label>
        <p className="field-help">{t("createOrganizationNote")}</p>
        <ErrorBox error={error} />
        <div className="form-actions">
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={onClose}
          >
            {t("cancel")}
          </button>
          <button
            type="submit"
            className="button primary"
            disabled={busy || !name.trim()}
          >
            <Icon name="plus" size={17} />
            {t(busy ? "waiting" : "createOrganization")}
          </button>
        </div>
      </form>
    </dialog>
  );
}
