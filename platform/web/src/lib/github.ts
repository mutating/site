export type GitHubStatus = {
  enabled: boolean;
  account: { id: number; login: string } | null;
};
export type GitHubInstallation = {
  id: number;
  login: string;
  repository_selection: "all" | "selected";
  suspended: boolean;
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
