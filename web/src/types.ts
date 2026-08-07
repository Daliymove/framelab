export type View = "gallery" | "generate" | "jobs" | "runtime";

export interface WorkerRuntime {
  pid: number;
  status: "running" | "stopped";
  started_at: string | null;
  heartbeat_at: string | null;
  heartbeat_age_seconds: number | null;
}

export interface RuntimeStatus {
  api: { pid: number; status: "running" };
  active_worker_count: number;
  workers: WorkerRuntime[];
}

export interface RemoteObject {
  provider: string;
  status: "pending" | "syncing" | "succeeded" | "failed" | "disabled";
  url: string;
  remote_id: string;
  synced_at: string | null;
  updated_at: string | null;
  last_error: string;
}

export interface Asset {
  id: string;
  filename: string;
  original_filename: string;
  mime_type: string;
  size_bytes: number;
  sha256: string;
  width: number | null;
  height: number | null;
  source: "upload" | "generated";
  title: string;
  notes: string;
  prompt_override: string;
  sync_enabled: boolean;
  created_at: string | null;
  updated_at: string | null;
  thumbnail_url: string;
  preview_url: string;
  original_url: string;
  tags: string[];
  remote: RemoteObject;
  metadata?: Record<string, unknown>;
  generation?: Job | null;
}

export interface AssetPage {
  items: Asset[];
  page: number;
  page_size: number;
  total: number;
  has_more: boolean;
}

export interface JobEvent {
  id: string;
  type: string;
  message: string;
  created_at: string | null;
  payload: Record<string, unknown>;
}

export interface Job {
  id: string;
  parent_job_id: string | null;
  asset_id: string | null;
  provider_id: string;
  provider_name: string;
  provider_url: string;
  model: string;
  prompt: string;
  size: string;
  quality: string;
  timeout_seconds: number;
  sync_enabled: boolean;
  status: "queued" | "submitted" | "running" | "succeeded" | "failed" | "canceled";
  progress_message: string;
  upstream_task_id: string | null;
  error_message: string;
  submitted_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  elapsed_ms: number | null;
  created_at: string | null;
  asset_url?: string;
  request?: Record<string, unknown>;
  response?: Record<string, unknown>;
  events?: JobEvent[];
}

export interface Config {
  ready: boolean;
  provider_count: number;
  provider_ready: boolean;
  provider: { id: string; name: string; base_url: string };
  imgbed: { enabled: boolean; configured: boolean; base_url: string };
  data_dir: string;
  database_path: string;
  asset_count: number;
  pending_sync_count: number;
  retries: number;
}

export interface Stats {
  assets: number;
  generated: number;
  uploaded: number;
  active_jobs: number;
  failed_jobs: number;
  synced: number;
}

export interface Provider {
  id: string;
  name: string;
  kind: string;
  base_url: string;
  api_key_env: string;
  enabled: boolean;
  ready: boolean;
}

export interface Tag {
  id: string;
  name: string;
}
