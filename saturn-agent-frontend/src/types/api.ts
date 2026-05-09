// Mirrors saturn_api/schemas/jobs.py — keep in sync.

export interface JobResponse {
  job_id: string;
  user_id: string;
  target_repo: string;
  stage: Stage;
  spec: Record<string, unknown>;
  plan: Plan | null;
  error: string | null;
  open_questions: string[];
  tokens_used: number;
  workspace_path: string | null;
  devbox_host: string | null;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
}

export interface JobListResponse {
  jobs: JobResponse[];
}

export interface JobEventResponse {
  job_id: string;
  stage: Stage;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface CreateJobRequest {
  spec: Record<string, unknown>;
  target_repo: string;
  devbox_host?: string;
  mock_script_path?: string;
  conventions_path?: string;
  target_repo_source?: string;
  target_repo_branch?: string;
}

export interface FilePlan {
  path: string;
  kind: string;
  description: string;
  depends_on?: string[];
}

export interface Plan {
  pipeline_name: string;
  target_repo: string;
  files: FilePlan[];
  open_questions?: string[];
  estimated_tokens?: number;
}

export type Stage =
  | "init"
  | "planning"
  | "generating"
  | "validating"
  | "repairing"
  | "testing"
  | "committing"
  | "opening_pr"
  | "succeeded"
  | "failed"
  | "paused_for_user";

export const VISIBLE_STAGES: Stage[] = [
  "init",
  "planning",
  "generating",
  "validating",
  "testing",
  "succeeded",
];

export const TERMINAL_STAGES: ReadonlySet<Stage> = new Set([
  "succeeded",
  "failed",
  "paused_for_user",
]);
