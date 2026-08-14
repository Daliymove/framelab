import type { Asset, AssetPage, Config, Job, Provider, RuntimeStatus, Stats, Tag } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = typeof payload.detail === "string" ? payload.detail : `请求失败（HTTP ${response.status}）`;
    throw new Error(message);
  }
  return payload as T;
}

export function getConfig() {
  return request<Config>("/api/config", { cache: "no-store" });
}

export function updateMediaDir(mediaDir: string) {
  return request<{ media_dir: string; migrated: boolean; message: string }>("/api/config/media-dir", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ media_dir: mediaDir }),
  });
}

export function getStats() {
  return request<Stats>("/api/stats", { cache: "no-store" });
}

export function getRuntime() {
  return request<RuntimeStatus>("/api/runtime", { cache: "no-store" });
}

export function startWorker() {
  return request<{ started: boolean; pid?: number; message?: string }>("/api/runtime/workers", { method: "POST" });
}

export function stopWorker(pid: number) {
  return request<{ stopping: boolean; pid: number }>(`/api/runtime/workers/${pid}/stop`, { method: "POST" });
}

export function getProviders() {
  return request<Provider[]>("/api/providers", { cache: "no-store" });
}

export function getTags() {
  return request<Tag[]>("/api/tags", { cache: "no-store" });
}

export function getAssets(params: { q?: string; source?: string; sync_status?: string; page?: number }) {
  const search = new URLSearchParams({ page_size: "40" });
  Object.entries(params).forEach(([key, value]) => {
    if (value && value !== "all") search.set(key, String(value));
  });
  return request<AssetPage>(`/api/assets?${search.toString()}`, { cache: "no-store" });
}

export function getAsset(id: string) {
  return request<Asset>(`/api/assets/${id}`, { cache: "no-store" });
}

export function deleteAsset(id: string) {
  return request<{ ok: boolean; id: string }>(`/api/assets/${id}`, { method: "DELETE" });
}

export function getJobs() {
  return request<Job[]>("/api/generation/jobs?limit=80", { cache: "no-store" });
}

export function createJob(payload: {
  prompt: string;
  model: string;
  size: string;
  quality: string;
  timeout: number;
  provider_id?: string;
  parent_job_id?: string;
  reference_asset_id?: string;
  sync_enabled: boolean;
}) {
  return request<Job>("/api/generation/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function retryJob(id: string) {
  return request<Job>(`/api/generation/jobs/${id}/retry`, { method: "POST" });
}

export function updateAsset(id: string, payload: Partial<Pick<Asset, "title" | "notes" | "prompt_override" | "sync_enabled">>) {
  return request<Asset>(`/api/assets/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function syncAsset(id: string) {
  return request<Asset>(`/api/assets/${id}/sync`, { method: "POST" });
}

export function bulkSync(assetIds: string[]) {
  return request<{ updated: number; queued: number }>("/api/assets/bulk-sync", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset_ids: assetIds }),
  });
}

export function bulkTags(assetIds: string[], tags: string[]) {
  return request<{ updated: number; tags: string[] }>("/api/assets/bulk-tags", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset_ids: assetIds, tags }),
  });
}

export function bulkDelete(assetIds: string[]) {
  return request<{ deleted: number }>("/api/assets/bulk-delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset_ids: assetIds }),
  });
}

export async function uploadAsset(file: File, values: { title: string; notes: string; tags: string; sync_enabled: boolean }) {
  const form = new FormData();
  form.append("file", file);
  form.append("title", values.title);
  form.append("notes", values.notes);
  form.append("tags_value", values.tags);
  form.append("sync_enabled", String(values.sync_enabled));
  return request<{ created: boolean; asset: Asset }>("/api/assets/upload", { method: "POST", body: form });
}
