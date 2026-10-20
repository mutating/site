import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, json } from "../lib/api";
import { useData } from "../lib/useData";
import { useI18n } from "../lib/i18n";
import { useSession } from "../lib/session";
import {
  githubAuthorizationURL,
  type GitHubInstallation,
  type GitHubRepository,
  type GitHubStatus,
} from "../lib/github";
import { Empty, ErrorBox, Heading, Loading } from "../components/UI";
import { CIRoutes } from "../components/CIRoutes";

export function Actions() {
  const { t } = useI18n();
  const { user } = useSession();
  const [query] = useSearchParams();
  const authorizationResult = query.get("github");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [installationId, setInstallationId] = useState<number | null>(null);
  const [installationPage, setInstallationPage] = useState(1);
  const [repositoryPagination, setRepositoryPagination] = useState<{ installationId: number; page: number } | null>(null);
  const status = useData(
    (signal) => api<GitHubStatus>("github/status", { signal }),
    user!.id,
  );
  const accountId = status.data?.account?.id;
  const installations = useData(
    (signal) => accountId
      ? api<{ installations: GitHubInstallation[]; total_count: number }>(
          `github/installations?page=${installationPage}`, { signal },
        )
      : Promise.resolve({ installations: [], total_count: 0 }),
    `${user!.id}/${accountId ?? ""}/${installationPage}`,
    true,
  );
  const selected = installations.data?.installations.find((row) => row.id === installationId && !row.suspended)
    ?? installations.data?.installations.find((row) => !row.suspended);
  const selectedId = selected?.id;
  const repositoryPage = repositoryPagination && repositoryPagination.installationId === selectedId ? repositoryPagination.page : 1;
  const repositories = useData(
    (signal) => accountId && selectedId && !selected?.suspended
      ? api<{ repositories: GitHubRepository[]; total_count: number }>(
          `github/installations/${selectedId}/repositories?page=${repositoryPage}`, { signal },
        )
      : Promise.resolve({ repositories: [], total_count: 0 }),
    `${user!.id}/${accountId ?? ""}/${selectedId ?? ""}/${repositoryPage}`,
    true,
  );
  async function authorize(path: "authorize" | "installations/authorize") {
    setError(null);
    setBusy(true);
    try {
      const response = await api<{ url: string }>(`github/${path}`, json("POST"));
      window.location.assign(githubAuthorizationURL(response.url));
    } catch (error) {
      setError(error);
      setBusy(false);
    }
  }
  async function disconnect() {
    setBusy(true);
    setError(null);
    try {
      await api("github/connection", json("DELETE"));
      setInstallationId(null);
      setInstallationPage(1);
      setRepositoryPagination(null);
      status.reload();
    } catch (error) {
      setError(error);
    } finally {
      setBusy(false);
    }
  }
  const refresh = () => {
    installations.reload();
    repositories.reload();
  };
  return (
    <>
      <Heading index={`03 / ${t("actionsTag")}`} title="Actions">
        {t("actionsLead")}
      </Heading>
      <ErrorBox error={error} />
      {authorizationResult === "cancelled" && <p role="status">{t("githubAuthorizationCancelled")}</p>}
      {authorizationResult === "authorization_failed" && <p role="alert">{t("githubAuthorizationFailed")}</p>}
      {status.loading ? <Loading /> : status.error ? (
        <ErrorBox error={status.error} retry={status.reload} />
      ) : !status.data?.enabled ? (
        <Empty title={t("actionsUnavailable")}>{t("actionsUnavailableLead")}</Empty>
      ) : (
        <div className="actions-panels">
          <section className="panel actions-connection" aria-busy={busy}>
            <div>
              <p className="eyebrow">GitHub</p>
              <h2>{status.data.account ? `@${status.data.account.login}` : t("connectGitHub")}</h2>
              <p className="muted">{status.data.account ? t("githubConnected") : t("githubConnectionLead")}</p>
              {status.data.account && <p className="muted">{t("githubDisconnectHelp")}</p>}
            </div>
            <div className="form-actions">
              <button className={`button ${status.data.account ? "secondary" : "primary"}`} disabled={busy} onClick={() => authorize("authorize")}>
                {status.data.account ? t("reconnectGitHub") : t("connectGitHub")}
              </button>
              {status.data.account && (
                <button className="button secondary" disabled={busy} onClick={disconnect}>
                  {t("disconnectGitHub")}
                </button>
              )}
            </div>
          </section>
          {status.data.account && (
            <section className="panel actions-repositories">
              <div className="actions-section-heading">
                <h2>{t("githubRepositories")}</h2>
                <div className="form-actions">
                  <button className="button secondary compact" disabled={busy} onClick={refresh}>{t("refresh")}</button>
                  <button className="button primary compact" disabled={busy} onClick={() => authorize("installations/authorize")}>{t("githubChooseRepositories")}</button>
                </div>
              </div>
              {installations.loading ? <Loading /> : installations.error ? (
                <ErrorBox error={installations.error} retry={installations.reload} />
              ) : !installations.data?.total_count ? (
                <p className="muted">{t("githubNoRepositories")}</p>
              ) : (
                <>
                  <label className="field">
                    {t("githubRepositoryOwner")}
                    <select value={selectedId ?? ""} onChange={(event) => {
                      setInstallationId(Number(event.target.value));
                      setRepositoryPagination(null);
                    }}>
                      <option value="" disabled>{t("githubSelectOwner")}</option>
                      {installations.data.installations.map((row) => (
                        <option key={row.id} value={row.id} disabled={row.suspended}>
                          {row.login}{row.suspended ? ` (${t("githubSuspended")})` : ""}
                        </option>
                      ))}
                    </select>
                  </label>
                  {(installationPage > 1 || installations.data.total_count > 100) && (
                    <div className="form-actions">
                      <button className="button secondary compact" disabled={installationPage === 1} onClick={() => { setInstallationPage((x) => x - 1); setInstallationId(null); setRepositoryPagination(null); }}>{t("previous")}</button>
                      <button className="button secondary compact" disabled={installationPage * 100 >= installations.data.total_count} onClick={() => { setInstallationPage((x) => x + 1); setInstallationId(null); setRepositoryPagination(null); }}>{t("next")}</button>
                    </div>
                  )}
                  {!selectedId && installations.data.installations.length > 0 ? (
                    <p className="muted" role="status">{t("githubInstallationsSuspended")}</p>
                  ) : repositories.loading ? <Loading /> : repositories.error ? (
                    <ErrorBox error={repositories.error} retry={repositories.reload} />
                  ) : (
                    <>
                      {!repositories.data?.repositories.length && <p className="muted">{t("githubNoRepositories")}</p>}
                      <div className="actions-repository-list">
                        {repositories.data?.repositories.map((repository) => (
                          <article className="actions-repository" key={repository.id}>
                            <a href={`https://github.com/${repository.full_name.split("/").map(encodeURIComponent).join("/")}`} target="_blank" rel="noreferrer">{repository.full_name}</a>
                            <span className="muted">{repository.private ? t("githubPrivate") : t("githubPublic")}</span>
                          </article>
                        ))}
                      </div>
                      {(repositoryPage > 1 || (repositories.data?.total_count ?? 0) > 100) && (
                        <div className="form-actions">
                          <button className="button secondary compact" disabled={repositoryPage === 1} onClick={() => setRepositoryPagination({ installationId: selectedId!, page: repositoryPage - 1 })}>{t("previous")}</button>
                          <button className="button secondary compact" disabled={repositoryPage * 100 >= (repositories.data?.total_count ?? 0)} onClick={() => setRepositoryPagination({ installationId: selectedId!, page: repositoryPage + 1 })}>{t("next")}</button>
                        </div>
                      )}
                    </>
                  )}
                </>
              )}
            </section>
          )}
          {status.data.account && <CIRoutes installationId={selectedId} repositories={repositories.data?.repositories ?? []} discoveryReady={!installations.loading && !installations.error && !repositories.loading && !repositories.error} />}
        </div>
      )}
    </>
  );
}
