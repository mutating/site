import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, ApiError, json } from "../lib/api";
import { useSession } from "../lib/session";
import { useI18n } from "../lib/i18n";
import type { User } from "../lib/types";
import { Avatar } from "../components/Avatar";
import { ConfirmDialog, ErrorBox, Heading, Icon } from "../components/UI";
import { ApiTokens } from "./ApiTokens";

const sections = ["personal", "account", "api-tokens"] as const;
type ProfileSection = typeof sections[number];

export function Profile() {
  const { t } = useI18n();
  const [params, setParams] = useSearchParams();
  const requested = params.get("section");
  const active = sections.find(section => section === requested) || "personal";
  const tabsRef = useRef<HTMLDivElement>(null);
  const previousSection = useRef(active);
  // History changes selection without replacing the surviving tab buttons.
  useEffect(() => {
    if (previousSection.current === active) return;
    previousSection.current = active;
    const focused = document.activeElement;
    if (focused === document.body || tabsRef.current?.contains(focused)) {
      document.getElementById(`profile-tab-${active}`)?.focus({ preventScroll: true });
    }
  }, [active]);
  const labels = [t("profileDetails"), t("profileAccount"), t("apiTokens")];
  const selectSection = (section: ProfileSection) => {
    if (section === active) return;
    const next = new URLSearchParams(params);
    if (section === "personal") next.delete("section");
    else next.set("section", section);
    setParams(next, { state: { keepScroll: true } });
  };
  return (
    <div className="profile-page">
      <div className="profile-heading">
        <Heading index={`05 / ${t("profile")}`} title={t("profile")}>
          {t("profileLead")}
        </Heading>
      </div>
      <div ref={tabsRef} className="profile-tabs" role="tablist" aria-label={t("profileSections")}>
        {sections.map((section, index) => (
          <button
            type="button"
            role="tab"
            id={`profile-tab-${section}`}
            aria-controls={`profile-panel-${section}`}
            aria-selected={active === section}
            tabIndex={active === section ? 0 : -1}
            key={section}
            onClick={() => selectSection(section)}
            onKeyDown={(event) => {
              if (event.altKey || event.ctrlKey || event.metaKey) return;
              let next: number;
              switch (event.key) {
                case "ArrowRight": next = (index + 1) % sections.length; break;
                case "ArrowLeft": next = (index + sections.length - 1) % sections.length; break;
                case "Home": next = 0; break;
                case "End": next = sections.length - 1; break;
                default: return;
              }
              event.preventDefault();
              document.getElementById(`profile-tab-${sections[next]}`)?.focus();
              selectSection(sections[next]);
            }}
          >
            {labels[index]}
          </button>
        ))}
      </div>
      <section
        id="profile-panel-personal"
        className="profile-personal-panel"
        role="tabpanel"
        aria-labelledby="profile-tab-personal"
        hidden={active !== "personal"}
      >
        <PersonalDetails />
      </section>
      <section
        id="profile-panel-account"
        className="profile-account-panel"
        role="tabpanel"
        aria-labelledby="profile-tab-account"
        hidden={active !== "account"}
      >
        {active === "account" && <AccountSettings />}
      </section>
      <section
        id="profile-panel-api-tokens"
        role="tabpanel"
        aria-labelledby="profile-tab-api-tokens"
        hidden={active !== "api-tokens"}
      >
        {active === "api-tokens" && <ApiTokens />}
      </section>
    </div>
  );
}

function PersonalDetails() {
  const { user, updateUser } = useSession();
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [success, setSuccess] = useState(false);
  const [avatarError, setAvatarError] = useState<unknown>(null);
  const [avatarSuccess, setAvatarSuccess] = useState(false);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const editRevision = useRef(0);
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
        await updateUser(() => api<User>("users/me/avatar", {
          method: "PUT",
          body: file,
          headers: { "Content-Type": file.type || "application/octet-stream" },
        }));
      } else {
        await updateUser(() => api("users/me/avatar", json("DELETE")));
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
      <div className="profile-panel">
        <section
          className="profile-section"
          aria-labelledby="profile-avatar-title"
        >
          <div className="profile-section-label">
            <h2 id="profile-avatar-title">{t("profileAvatar")}</h2>
            <p>{t("profileAvatarLead")}</p>
          </div>
          <div className="panel avatar-editor">
            <div className="avatar-controls" aria-busy={avatarBusy}>
              <Avatar large />
              <div>
                <div className="profile-avatar-actions">
                  <label
                    className={`button secondary compact file-button ${busy ? "is-disabled" : ""}`}
                  >
                    <Icon name="camera" size={15} />
                    {t(avatarBusy ? "waiting" : "uploadAvatar")}
                    <input
                      type="file"
                      accept="image/png,image/jpeg,image/webp"
                      aria-label={t("uploadAvatar")}
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
                </div>
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
        </section>
        <form
          className="profile-settings-form"
          onChange={() => {
            editRevision.current++;
            setSuccess(false);
          }}
          onSubmit={async (e) => {
            e.preventDefault();
            if (busy) return;
            setBusy(true);
            setError(null);
            setSuccess(false);
            const values = Object.fromEntries(new FormData(e.currentTarget));
            const submittedRevision = editRevision.current;
            try {
              await updateUser(() => api<User>(
                  `users/${user!.id}`,
                  json("PATCH", {
                    ...values,
                    nickname: values.nickname || null,
                  }),
                ));
              // A late response only confirms the version this request submitted.
              setSuccess(editRevision.current === submittedRevision);
            } catch (err) {
              setError(err);
            } finally {
              setBusy(false);
            }
          }}
        >
          <section
            className="profile-section"
            aria-labelledby="profile-details-title"
          >
            <div className="profile-section-label">
              <h2 id="profile-details-title">{t("profileDetails")}</h2>
              <p>{t("profileDetailsLead")}</p>
            </div>
            <div className="panel profile-section-content">
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
            </div>
          </section>
          <section
            className="profile-section"
            aria-labelledby="profile-email-title"
          >
            <div className="profile-section-label">
              <h2 id="profile-email-title">{t("profileEmail")}</h2>
              <p>{t("profileEmailLead")}</p>
            </div>
            <div className="panel profile-section-content">
              <label className="field">
                <span className="profile-email-label">
                  {t("email")}
                  <Icon name="lock" size={13} />
                </span>
                <input value={user!.email} readOnly autoComplete="email" />
              </label>
            </div>
          </section>
          <div className="profile-form-actions">
            <div className="profile-form-feedback">
              <ErrorBox error={error} />
              {success && (
                <p className="success-message" role="status">
                  {t("saved")}
                </p>
              )}
            </div>
            <button className="button primary" disabled={busy}>
              {t(busy ? "waiting" : "save")}
            </button>
          </div>
        </form>
      </div>
  );
}

function AccountSettings() {
  const { user, setUser } = useSession();
  const { t } = useI18n();
  const [deleting, setDeleting] = useState(false);
  return (
    <>
      <h2 className="profile-account-title">{t("accountManagement")}</h2>
      <div className="panel profile-account-management">
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
      </div>
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
