import { useEffect, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../lib/api";
import { useI18n, type MessageKey } from "../lib/i18n";

export function Icon({ name, size = 20 }: { name: string; size?: number }) {
  const shapes: Record<string, ReactNode> = {
    play: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="m10 8 6 4-6 4Z" />
      </>
    ),
    grid: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1.5" />
        <rect x="14" y="3" width="7" height="7" rx="1.5" />
        <rect x="3" y="14" width="7" height="7" rx="1.5" />
        <rect x="14" y="14" width="7" height="7" rx="1.5" />
      </>
    ),
    cells: (
      <>
        <path d="m12 2 9 5v10l-9 5-9-5V7l9-5ZM3 7l9 5 9-5M12 12v10" />
        <path d="m7 4.8 10 5.5" />
      </>
    ),
    cloud: <path d="M6 18a4 4 0 1 1 .5-8A6 6 0 0 1 18 8a5 5 0 0 1 0 10H6Z" />,
    users: (
      <>
        <circle cx="9" cy="8" r="3" />
        <path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6M18 15a4 4 0 0 1 3 4v2" />
      </>
    ),
    user: (
      <>
        <circle cx="12" cy="8" r="4" />
        <path d="M4 22v-3a8 8 0 0 1 16 0v3" />
      </>
    ),
    arrow: <path d="M4 12h16m-6-6 6 6-6 6" />,
    cpu: (
      <>
        <rect x="5" y="5" width="14" height="14" rx="2" />
        <rect x="9" y="9" width="6" height="6" rx="1" />
        <path d="M9 2v3m6-3v3M9 19v3m6-3v3M2 9h3m-3 6h3m14-6h3m-3 6h3" />
      </>
    ),
    memory: (
      <>
        <rect x="2" y="6" width="20" height="11" rx="2" />
        <path d="M6 9v5m4-5v5m4-5v5m4-5v5M6 17v3m4-3v3m4-3v3m4-3v3" />
      </>
    ),
    computer: (
      <>
        <rect x="2" y="3" width="20" height="14" rx="2" />
        <path d="M8 22h8m-4-5v5" />
      </>
    ),
    plus: <path d="M12 4v16M4 12h16" />,
    search: (
      <>
        <circle cx="10.5" cy="10.5" r="6.5" />
        <path d="m16 16 5 5" />
      </>
    ),
    trash: (
      <>
        <path d="M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7m4-7v7" />
      </>
    ),
    close: <path d="m6 6 12 12M6 18 18 6" />,
    check: <path d="m5 12 4 4L19 6" />,
    building: <><rect x="7" y="3" width="14" height="19" rx="2" /><path d="M7 9H3v13h6M11 7h2m4 0h0m-6 4h2m4 0h0m-6 4h2m4 0h0m-4 7v-3h3v3" /></>,
    exit: (
      <>
        <path d="M10 3H4v18h6m-1-9h13m-5-5 5 5-5 5" />
      </>
    ),
    refresh: (
      <>
        <path d="M20 7A9 9 0 0 0 4 6m0 11a9 9 0 0 0 16 1M20 2v5h-5M4 22v-5h5" />
      </>
    ),
    eye: (
      <>
        <path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12Z" />
        <circle cx="12" cy="12" r="3" />
      </>
    ),
    camera: (
      <>
        <path d="M8 5 9.5 3h5L16 5h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z" />
        <circle cx="12" cy="13" r="4" />
      </>
    ),
    lock: (
      <>
        <rect x="4" y="10" width="16" height="12" rx="2" />
        <path d="M8 10V6a4 4 0 0 1 8 0v4M12 15v2" />
      </>
    ),
    "chevron-down": <path d="m6 9 6 6 6-6" />,
    key: <><circle cx="15" cy="8" r="5" /><path d="m11.5 11.5-8 8V22H7v-3h3v-3l2-2" /></>,
    copy: <><rect x="8" y="8" width="12" height="13" rx="2" /><path d="M16 8V3H3v13h5" /></>,
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {shapes[name] || shapes.grid}
    </svg>
  );
}
export function Heading({
  index,
  title,
  children,
  action,
}: {
  index: string;
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        <p className="eyebrow">{index}</p>
        <h1>
          {title}
          <span className="hot">.</span>
        </h1>
        {children && <p className="lede">{children}</p>}
      </div>
      {action}
    </div>
  );
}
export function Loading() {
  const { t } = useI18n();
  return (
    <div className="loading" role="status">
      <span className="loading-dot" />
      {t("loading")}
    </div>
  );
}
export function ErrorBox({
  error,
  retry,
  conflict = "conflict",
}: {
  error: unknown;
  retry?: () => void;
  conflict?: MessageKey;
}) {
  const { t } = useI18n();
  if (!error) return null;
  const status = error instanceof ApiError ? error.status : 0;
  let key: MessageKey =
    status === 401
      ? "invalidCredentials"
      : status === 409
        ? conflict
        : status === 403
          ? "forbidden"
          : status === 422
            ? "invalidInput"
            : status === 413
              ? "avatarSize"
              : "unavailable";
  if (error instanceof ApiError && error.detail === "Registration is disabled")
    key = "registrationDisabled";
  if (status === 404) key = "notFound";
  if (status === 410) key = "invitationUnavailable";
  if (
    error instanceof ApiError &&
    error.detail === "Invitation belongs to another account"
  )
    key = "invitationWrongAccount";
  if (
    error instanceof ApiError &&
    error.detail === "A company must retain at least one creator"
  )
    key = "leaveBlocked";
  if (
    error instanceof ApiError &&
    error.detail === "Leave company memberships before deleting the user"
  )
    key = "leaveBeforeDelete";
  if (
    error instanceof ApiError &&
    error.detail === "Other members must leave before deleting the company"
  )
    key = "companyMembersBlockDelete";
  if (
    error instanceof ApiError &&
    error.detail ===
      "Remove company token ownerships before deleting the company"
  )
    key = "companyTokensBlockDelete";
  return (
    <div className="error-box" role="alert">
      <span>{t(key)}</span>
      {retry && (
        <button className="text-button" onClick={retry}>
          {t("retry")} ↻
        </button>
      )}
    </div>
  );
}
export function Empty({
  icon = "cells",
  title,
  children,
}: {
  icon?: string;
  title: string;
  children?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <span className="empty-icon">
        <Icon name={icon} size={32} />
      </span>
      <h2>{title}</h2>
      {children && <p>{children}</p>}
    </div>
  );
}
export function ConfirmDialog({
  title,
  value,
  warning,
  onConfirm,
  onClose,
  conflict = "blockedDelete",
}: {
  title: string;
  value: string;
  warning: string;
  onConfirm: () => Promise<void>;
  onClose: () => void;
  conflict?: MessageKey;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const { t } = useI18n();
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
      className="confirm-dialog"
      aria-labelledby="confirmation-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
    >
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          if (text !== value || busy) return;
          setBusy(true);
          setError(null);
          try {
            await onConfirm();
            onClose();
          } catch (err) {
            setError(err);
            setBusy(false);
          }
        }}
      >
        <p className="eyebrow">{t("account")}</p>
        <h2 id="confirmation-title">{title}</h2>
        <p className="muted">{warning}</p>
        <label className="field">
          {t("typeToConfirm")}{" "}
          <strong className="confirm-value">{value}</strong>
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            autoComplete="off"
            autoFocus
            disabled={busy}
          />
        </label>
        <ErrorBox error={error} conflict={conflict} />
        <div className="form-actions">
          <button
            type="button"
            className="button secondary"
            disabled={busy}
            onClick={onClose}
          >
            {t("cancel")}
          </button>
          <button className="button danger" disabled={text !== value || busy}>
            {busy ? t("waiting") : t("confirm")}
          </button>
        </div>
      </form>
    </dialog>
  );
}
