import { useId, useLayoutEffect, useRef, useState } from "react";
import { ApiError } from "../lib/api";
import { organizationPoolLabels, poolLabel, PoolMoveCheckError, useComputeCompanies } from "../lib/compute";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";
import type { ConnectedComputer } from "../lib/types";
import { ErrorBox, Icon, Loading, useModalFocus } from "./UI";

export function MoveComputerDialog({
  computer,
  currentComputer,
  checkingConnection,
  connectionError,
  onMove,
  onClose,
  restoreFocus,
  reloadComputers,
  poolUpdating,
  retryKey,
  retryMove,
}: {
  computer: ConnectedComputer;
  currentComputer: ConnectedComputer | undefined;
  checkingConnection: boolean;
  connectionError: unknown;
  onMove: (companyId: string | null) => Promise<void>;
  onClose: () => void;
  restoreFocus: () => void;
  reloadComputers: () => void;
  poolUpdating: boolean;
  retryKey?: string;
  retryMove?: () => Promise<boolean>;
}) {
  const { t } = useI18n();
  const companies = useComputeCompanies(useSession().user!.id);
  const heading = useId();
  const ref = useRef<HTMLDialogElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const submitButton = useRef<HTMLButtonElement>(null);
  const retryButton = useRef<HTMLButtonElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const lastFocused = useRef<HTMLElement | null>(null);
  const submittedBy = useRef<"submit" | "retry" | null>(null);
  const inFlight = useRef(false);
  const [selection, setSelection] = useState("");
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const error = failure instanceof PoolMoveCheckError
    ? failure.idempotencyKey === retryKey ? failure.cause : null
    : failure;
  const current = computer.pool?.company_id;
  const options = (companies.data ?? [])
    .filter((company) => company.id !== current && company.my_permissions.includes("resources.manage"));
  const personal = t("personalPool");
  const organization = t("organizationType");
  const optionLabels = organizationPoolLabels(options, personal, organization);
  const stale = error instanceof ApiError && error.detail === "Computer pool changed";
  const blocked = error instanceof ApiError && error.detail === "Computer has reserved resources";
  const pending = error instanceof ApiError && error.detail === "Computer has an unfinished native transfer";
  const cancelled = error instanceof ApiError && error.detail === "Computer pool change was cancelled";
  const denied = error instanceof ApiError && [403, 404].includes(error.status);
  const sourceChanged = Boolean(currentComputer?.pool && (
    currentComputer.pool.company_id !== current || currentComputer.pool_revision !== computer.pool_revision
  ));
  const checkingPermissions = denied && (
    checkingConnection || Boolean(connectionError) || companies.loading || Boolean(companies.error)
  );
  const unavailable = !currentComputer?.can_move || sourceChanged || stale || pending || checkingPermissions || poolUpdating;
  const connectionMessage = sourceChanged ? "computerPoolChanged"
    : currentComputer?.transfer_pending ? "computerTransferPending"
    : currentComputer && !currentComputer.can_move ? "forbidden"
    : !currentComputer && !checkingConnection && !connectionError ? "notFound"
    : null;
  const ready = !busy && !unavailable && !denied && (
    selection === "personal" ? Boolean(current) : options.some((company) => company.id === selection)
  );
  const name = computer.name || computer.id.slice(0, 8);
  const confirm = async (move: () => Promise<void>, source: "submit" | "retry") => {
    if (inFlight.current) return;
    inFlight.current = true;
    submittedBy.current = source;
    title.current?.focus({ preventScroll: true });
    setBusy(true);
    setFailure(null);
    try {
      await move();
      onClose();
    } catch (err) {
      const cause = err instanceof PoolMoveCheckError ? err.cause : err;
      if (cause instanceof ApiError && [403, 404].includes(cause.status)) {
        setSelection("");
        companies.reload();
      }
      setFailure(err);
      setBusy(false);
      inFlight.current = false;
    }
  };
  useLayoutEffect(() => {
    if (busy) return;
    const focused = document.activeElement;
    if (submittedBy.current) {
      const control = submittedBy.current === "retry" ? retryButton.current : submitButton.current;
      submittedBy.current = null;
      if (focused !== title.current && focused !== ref.current && focused !== document.body) return;
      (control && !control.disabled ? control : closeButton.current)?.focus({ preventScroll: true });
    } else if (lastFocused.current && (!lastFocused.current.isConnected || lastFocused.current.matches(":disabled")) &&
        (focused === lastFocused.current || focused === ref.current || focused === document.body)) {
      closeButton.current?.focus({ preventScroll: true });
    }
  });

  useModalFocus(ref, restoreFocus);
  return (
    <dialog
      ref={ref}
      className="confirm-dialog move-pool-dialog"
      aria-labelledby={heading}
      onFocusCapture={(event) => {
        if (event.target instanceof HTMLElement) lastFocused.current = event.target;
      }}
      onCancel={(event) => {
        event.preventDefault();
        if (!inFlight.current) onClose();
      }}
    >
      <form onSubmit={(event) => {
        event.preventDefault();
        if (ready) void confirm(() => onMove(selection === "personal" ? null : selection), "submit");
      }}>
        <header className="move-pool-header">
          <div>
            <h2 id={heading} ref={title} tabIndex={-1}>{t("changePool")}</h2>
            <p className="muted">{name}</p>
          </div>
          <button ref={closeButton} type="button" className="button secondary compact" aria-label={t("closeDialog")} disabled={busy} onClick={onClose}>
            <Icon name="close" size={16} />
          </button>
        </header>
        <p className="muted">
          {t("currentPool")} <strong>{poolUpdating || (!currentComputer && checkingConnection) ? t("poolUpdating") : currentComputer?.pool ? poolLabel(currentComputer.pool.name, personal, organization) : t("unavailablePool")}</strong>
        </p>
        <label className="field">
          {t("newPool")}
          <select
            autoFocus
            value={selection}
            disabled={busy || unavailable}
            onChange={(event) => {
              setSelection(event.target.value);
              setFailure(null);
            }}
          >
            <option value="">{t("choosePool")}</option>
            {current && <option value="personal">{personal}</option>}
            {options.map(({ id }) => (
              <option key={id} value={id}>{optionLabels.get(id)}</option>
            ))}
          </select>
        </label>
        {companies.loading && !companies.data && <Loading />}
        {!current && companies.data && !options.length && <p className="field-help">{t("noPoolDestinations")}</p>}
        {connectionMessage && !error && !busy && <p className="field-help" role="alert">{t(connectionMessage)}</p>}
        <ErrorBox error={(denied || stale || pending || poolUpdating || !currentComputer) && connectionError} retry={reloadComputers} />
        <ErrorBox error={companies.error} retry={companies.reload} />
        <ErrorBox
          error={error}
          retryRef={retryButton}
          retry={retryMove && !busy ? () => void confirm(async () => {
            if (!await retryMove()) throw new ApiError(409, "Computer pool change was cancelled");
          }, "retry") : undefined}
          conflict={stale ? "computerPoolChanged" : blocked ? "computerBusy" : pending ? "computerTransferPending" : cancelled ? "poolChangeCancelled" : "conflict"}
        />
        <div className="form-actions">
          <button type="button" className="button secondary" disabled={busy} onClick={onClose}>{t("cancel")}</button>
          <button ref={submitButton} type="submit" className="button primary" disabled={!ready}>{t(busy ? "waiting" : "changePool")}</button>
        </div>
      </form>
    </dialog>
  );
}
