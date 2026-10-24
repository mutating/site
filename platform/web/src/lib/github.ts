import type { MemberProfile } from "./types";
// Aggregate discovery has a 60s provider budget plus repository/access verification.
export const GITHUB_DISCOVERY_TIMEOUT_MS = 90_000;
export type GitHubStatus = {
  enabled: boolean;
  account: { id: number; login: string } | null;
};
export type GitHubRepository = {
  id: number;
  full_name: string;
  private: boolean;
  default_branch: string;
  can_administer: boolean;
};

export function githubAuthorizationURL(value: string): string {
  const url = new URL(value);
  if (url.origin !== "https://github.com" || url.username || url.password)
    throw new Error("Invalid GitHub authorization URL");
  return url.href;
}

export type CIProfile = {
  cpu_millis: number;
  memory_bytes: number;
  disk_bytes: number;
  preparation_seconds: number;
  timeout_seconds: number;
  computer_ids: string[];
};
export type CIRoute = {
  id: string;
  repository: string;
  workflow_path: string;
  job_key: string;
  ref: string;
  company_id: string | null;
  computer_ids: string[];
  cpu_millis: number;
  memory_bytes: number;
  disk_bytes: number;
  preparation_seconds: number;
  timeout_seconds: number;
  max_parallel: number;
  enabled: boolean;
  installation_id: number;
  repository_id: number;
  workflow_paths: string[] | null;
  workflow_profiles?: Record<string, Partial<CIProfile>>;
  revision: number;
  created_at: string;
  active_runs?: boolean;
};
export type CIRun = {
  id: string;
  run_id: number;
  run_attempt: number;
  instance: string;
  status: string;
  reason: string | null;
  isolate_id: string | null;
  url: string;
  created_at: string;
};

export type RepositoryState = "unconfigured" | "pending" | "connected" | "disabled";
export type CatalogRepository = GitHubRepository & {
  installation_id: number;
  owner_login: string;
  owner_type: "personal" | "organization";
  state: RepositoryState;
  binding_id: string | null;
  last_run: (CIRun & { workflow_path: string }) | null;
};
export type RepositoryConfiguration = {
  repository: CatalogRepository;
  branches: string[];
  workflows: { path: string; name: string; jobs: string[]; parse_error: boolean }[];
  bindings: CIRoute[];
  binding_id?: string | null;
  state: RepositoryState;
  last_run: CatalogRepository["last_run"];
};
export type BindingRevision = {
  id: string;
  revision: number;
  action: string;
  created_at: string;
  actor: MemberProfile | null;
  configuration: Record<string, unknown>;
  changes: { field: string; before: unknown; after: unknown }[];
};
