import { useState } from "react";
import { api, ApiError, json } from "../lib/api";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import type { User } from "../lib/types";
import { Avatar } from "../components/Layout";
import { ConfirmDialog, ErrorBox, Heading, Icon } from "../components/UI";
export function Profile() {
  const { user, setUser } = useSession();
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [logoutError, setLogoutError] = useState<unknown>(null);
  const [success, setSuccess] = useState(false);
  const [avatarError, setAvatarError] = useState<unknown>(null);
  const [avatarSuccess, setAvatarSuccess] = useState(false);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [deleting, setDeleting] = useState(false);
  async function changeAvatar(file?: File) {
    setAvatarError(null);
    setAvatarSuccess(false);
    if (file && file.size > 2 * 1024 * 1024) {
      setAvatarError(new ApiError(413, ""));
      return;
    }
    setBusy(true);
    setAvatarBusy(true);
    try {
      if (file) {
        const updated = await api<User>("users/me/avatar", {
          method: "PUT",
          body: file,
          headers: { "Content-Type": file.type || "application/octet-stream" },
        });
        setUser(updated);
      } else {
        await api("users/me/avatar", json("DELETE"));
        setUser({ ...user!, avatar_version: null });
      }
      setAvatarSuccess(true);
    } catch (e) {
      setAvatarError(e);
    } finally {
      setBusy(false);
      setAvatarBusy(false);
    }
  }
  return (
    <>
      <Heading
        index={`05 / ${t("profile")}`}
        title={t("profile")}
        action={
          <button
            className="button secondary compact"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setLogoutError(null);
              try {
                await api("session", json("DELETE"));
                setUser(null);
              } catch (e) {
                setLogoutError(e);
                setBusy(false);
              }
            }}
          >
            <Icon name="exit" size={16} />
            {t("logout")}
          </button>
        }
      >
        {t("profileLead")}
      </Heading>
      <ErrorBox error={logoutError} />
      <section className="panel profile-panel">
        <div className="avatar-editor">
          <div className="avatar-controls" aria-busy={avatarBusy}>
            <Avatar large />
            <div>
              <label
                className={`button secondary compact file-button ${busy ? "is-disabled" : ""}`}
              >
                {t(avatarBusy ? "waiting" : "uploadAvatar")}
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  disabled={busy}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void changeAvatar(file);
                    e.target.value = "";
                  }}
                />
              </label>
              {user!.avatar_version && (
                <button
                  className="text-button"
                  disabled={busy}
                  onClick={() => changeAvatar()}
                >
                  {t("removeAvatar")}
                </button>
              )}
              <p className="field-help">{t("avatarHelp")}</p>
            </div>
          </div>
          <ErrorBox error={avatarError} />
          {avatarSuccess && (
            <p className="success-message" role="status">
              {t("saved")}
            </p>
          )}
        </div>
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (busy) return;
            setBusy(true);
            setError(null);
            setSuccess(false);
            const values = Object.fromEntries(new FormData(e.currentTarget));
            try {
              setUser(
                await api<User>(
                  `users/${user!.id}`,
                  json("PATCH", {
                    ...values,
                    nickname: values.nickname || null,
                  }),
                ),
              );
              setSuccess(true);
            } catch (err) {
              setError(err);
            } finally {
              setBusy(false);
            }
          }}
        >
          <div className="field-row">
            <label className="field">
              {t("firstName")}
              <input
                name="first_name"
                defaultValue={user!.first_name}
                autoComplete="given-name"
                maxLength={100}
              />
            </label>
            <label className="field">
              {t("lastName")}
              <input
                name="last_name"
                defaultValue={user!.last_name}
                autoComplete="family-name"
                maxLength={100}
              />
            </label>
          </div>
          <label className="field">
            {t("nickname")}
            <input
              aria-label={t("nickname")}
              name="nickname"
              defaultValue={user!.nickname || ""}
              maxLength={64}
              pattern="[A-Za-z0-9_][A-Za-z0-9_.\-]*"
              autoComplete="username"
            />
            <span className="field-help">{t("nicknameHelp")}</span>
          </label>
          <label className="field">
            {t("email")}
            <input value={user!.email} readOnly autoComplete="email" />
          </label>
          <ErrorBox error={error} />
          {success && (
            <p className="success-message" role="status">
              {t("saved")}
            </p>
          )}
          <button className="button primary" disabled={busy}>
            {t(busy ? "waiting" : "save")}
          </button>
        </form>
      </section>
      <section className="danger-zone">
        <h2>{t("accountDeletion")}</h2>
        <p>{t("deletionLead")}</p>
        <button
          className="button secondary danger-text"
          onClick={(event) => {
            event.currentTarget.focus({ preventScroll: true });
            setDeleting(true);
          }}
        >
          {t("deleteAccount")}
        </button>
      </section>
      {deleting && (
        <ConfirmDialog
          title={t("confirmDelete")}
          value={user!.email}
          warning={t("deletionLead")}
          onClose={() => setDeleting(false)}
          onConfirm={async () => {
            await api(`users/${user!.id}`, json("DELETE"));
            setUser(null);
          }}
        />
      )}
    </>
  );
}
