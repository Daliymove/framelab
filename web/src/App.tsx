import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Activity,
  Archive,
  ArrowUpRight,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleDot,
  CloudUpload,
  Copy,
  Database,
  Download,
  ExternalLink,
  FileImage,
  Filter,
  FolderOpen,
  ImagePlus,
  Images,
  Info,
  LayoutGrid,
  ListTodo,
  LoaderCircle,
  Menu,
  MoreHorizontal,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Server,
  Sparkles,
  Square,
  Tag as TagIcon,
  Terminal,
  Timer,
  Trash2,
  Upload,
  WandSparkles,
  X,
} from "lucide-react";
import {
  bulkDelete,
  bulkSync,
  bulkTags,
  cancelJob,
  createJob,
  deleteAsset,
  deleteJob,
  getAsset,
  getAssets,
  getConfig,
  getJob,
  getJobs,
  getRuntime,
  getProviders,
  getStats,
  getTags,
  retryJob,
  startWorker,
  stopWorker,
  syncAsset,
  updateMediaDir,
  updateAsset,
  uploadAsset,
} from "./api";
import type { Asset, AssetPage, Config, Job, Provider, RuntimeStatus, Stats, Tag, View } from "./types";

function formatBytes(bytes: number) {
  if (!Number.isFinite(bytes)) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
  return `${(bytes / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function formatDate(value: string | null) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function formatDuration(milliseconds: number | null) {
  if (milliseconds === null || milliseconds === undefined) return "-";
  const seconds = milliseconds / 1000;
  return seconds < 60 ? `${seconds.toFixed(1)} s` : `${Math.floor(seconds / 60)}m ${(seconds % 60).toFixed(0)}s`;
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    queued: "排队中",
    submitted: "已提交",
    running: "处理中",
    succeeded: "已完成",
    failed: "失败",
    canceled: "已取消",
    pending: "待同步",
    syncing: "同步中",
    disabled: "仅本地",
  };
  return labels[status] || status;
}

function isSyncInProgress(status: string) {
  return status === "pending" || status === "syncing";
}

function StatusIcon({ status }: { status: string }) {
  if (status === "succeeded") return <CheckCircle2 size={14} />;
  if (status === "failed") return <AlertTriangle size={14} />;
  if (status === "pending" || status === "syncing" || status === "running" || status === "submitted") {
    return <LoaderCircle className="spin" size={14} />;
  }
  return <CircleDot size={14} />;
}

function NavButton({ active, icon, label, onClick, count }: { active: boolean; icon: React.ReactNode; label: string; onClick: () => void; count?: number }) {
  return (
    <button type="button" className={`nav-button ${active ? "active" : ""}`} onClick={onClick}>
      {icon}
      <span>{label}</span>
      {count ? <b>{count}</b> : null}
    </button>
  );
}

function StatTile({ label, value, accent }: { label: string; value: number | string; accent?: string }) {
  return (
    <div className="stat-tile">
      <span className={`stat-accent ${accent || ""}`} />
      <div>
        <small>{label}</small>
        <strong>{value}</strong>
      </div>
    </div>
  );
}

function AssetCard({ asset, selected, onSelect, onOpen }: { asset: Asset; selected: boolean; onSelect: () => void; onOpen: () => void }) {
  return (
    <article className={`asset-card ${selected ? "selected" : ""}`}>
      <div className="asset-image-wrap">
        <img src={asset.thumbnail_url} alt={asset.title || asset.original_filename} loading="lazy" />
        <button type="button" className="select-check" onClick={onSelect} aria-label="选择图片">
          {selected ? <Check size={14} /> : null}
        </button>
        <span className={`source-chip ${asset.source}`}><span>{asset.source === "generated" ? "AI" : "UP"}</span></span>
        <span className={`sync-chip ${asset.remote.status}`} title={statusLabel(asset.remote.status)}><StatusIcon status={asset.remote.status} /></span>
      </div>
      <button type="button" className="asset-card-body" onClick={onOpen}>
        <strong>{asset.title || asset.original_filename}</strong>
        <span>{asset.width && asset.height ? `${asset.width} × ${asset.height}` : "图片"} · {formatBytes(asset.size_bytes)}</span>
        <time>{formatDate(asset.created_at)}</time>
        {asset.tags.length ? <div className="tag-row">{asset.tags.slice(0, 3).map((tag) => <em key={tag}>{tag}</em>)}</div> : null}
      </button>
    </article>
  );
}

function EmptyGallery({ onGenerate, onUpload }: { onGenerate: () => void; onUpload: () => void }) {
  return (
    <div className="empty-gallery">
      <div className="empty-mark"><Images size={36} strokeWidth={1.2} /><span><Sparkles size={15} /></span></div>
      <p className="kicker">LOCAL ASSET ARCHIVE</p>
      <h2>从第一张图开始建立你的回溯库</h2>
      <p>生成或上传图片后，prompt、provider、耗时和图床状态都会和原图一起保存。</p>
      <div className="empty-actions">
        <button type="button" className="button primary" onClick={onGenerate}><WandSparkles size={16} />开始生成</button>
        <button type="button" className="button quiet" onClick={onUpload}><Upload size={16} />上传图片</button>
      </div>
    </div>
  );
}

function AssetDrawer({ assetId, onClose, onChanged, setNotice }: { assetId: string; onClose: () => void; onChanged: () => void; setNotice: (value: string) => void }) {
  const queryClient = useQueryClient();
  const detail = useQuery({
    queryKey: ["asset", assetId],
    queryFn: () => getAsset(assetId),
    refetchInterval: (query) => {
      const current = query.state.data as Asset | undefined;
      return current && isSyncInProgress(current.remote.status) ? 1500 : false;
    },
  });
  const [title, setTitle] = useState("");
  const [notes, setNotes] = useState("");
  const [promptOverride, setPromptOverride] = useState("");
  const [syncEnabled, setSyncEnabled] = useState(true);

  useEffect(() => {
    if (detail.data) {
      setTitle(detail.data.title);
      setNotes(detail.data.notes);
      setPromptOverride(detail.data.prompt_override);
      setSyncEnabled(detail.data.sync_enabled);
    }
  }, [detail.data]);

  const save = useMutation({
    mutationFn: () => updateAsset(assetId, { title, notes, prompt_override: promptOverride, sync_enabled: syncEnabled }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["asset", assetId] });
      queryClient.invalidateQueries({ queryKey: ["assets"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      setNotice("图片信息已保存");
      onChanged();
    },
  });
  const sync = useMutation({
    mutationFn: () => syncAsset(assetId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["asset", assetId] });
      queryClient.invalidateQueries({ queryKey: ["assets"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      setNotice("已加入图床同步队列");
    },
  });
  const asset = detail.data;
  const remove = useMutation({
    mutationFn: () => deleteAsset(assetId),
    onSuccess: () => {
      queryClient.removeQueries({ queryKey: ["asset", assetId] });
      queryClient.invalidateQueries({ queryKey: ["assets"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      setNotice("图片已从图库删除");
      onChanged();
      onClose();
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : "删除图片失败"),
  });
  const handleDelete = () => {
    if (!asset || remove.isPending) return;
    const name = asset.title || asset.original_filename;
    if (window.confirm(`确定删除“${name}”？图片将从图库中移除。`)) remove.mutate();
  };

  return (
    <div className="drawer-backdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
      <aside className="asset-drawer">
        <div className="drawer-head">
          <div><p className="kicker">ASSET TRACE</p><h2>图片详情</h2></div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭"><X size={18} /></button>
        </div>
        {detail.isLoading ? <div className="drawer-loading"><LoaderCircle className="spin" /><span>读取元数据...</span></div> : null}
        {detail.error ? <div className="inline-error"><AlertTriangle size={16} />无法读取图片详情</div> : null}
        {asset ? <>
          <div className="drawer-preview"><img src={asset.preview_url} alt={asset.title || asset.filename} /></div>
          <div className="drawer-actions">
            <a className="button quiet" href={asset.original_url} download={asset.filename}><Download size={15} />下载原图</a>
            {asset.remote.url ? <a className="button quiet" href={asset.remote.url} target="_blank" rel="noreferrer"><ExternalLink size={15} />打开图床</a> : <button type="button" className="button quiet" onClick={() => sync.mutate()} disabled={sync.isPending}><CloudUpload size={15} />{sync.isPending ? "排队中" : "同步图床"}</button>}
            <button type="button" className="button danger" onClick={handleDelete} disabled={remove.isPending}><Trash2 size={15} />{remove.isPending ? "删除中" : "删除图片"}</button>
          </div>
          <section className="drawer-section">
            <div className="section-label"><Pencil size={14} />整理信息</div>
            <label className="input-label">标题<input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="给这张图一个名字" /></label>
            <label className="input-label">备注<textarea value={notes} onChange={(event) => setNotes(event.target.value)} rows={3} placeholder="记录用途、灵感或修改方向" /></label>
            <label className="input-label">可编辑 prompt<input value={promptOverride} onChange={(event) => setPromptOverride(event.target.value)} placeholder="保留原始 prompt，另存修改版本" /></label>
            <label className="toggle-row"><input type="checkbox" checked={syncEnabled} onChange={(event) => setSyncEnabled(event.target.checked)} /><span className="toggle-visual" /><span>自动同步到 cloudflare-imgbed</span></label>
            <button type="button" className="button primary full" onClick={() => save.mutate()} disabled={save.isPending}><Check size={15} />{save.isPending ? "保存中" : "保存整理信息"}</button>
          </section>
          <section className="drawer-section trace-section">
            <div className="section-label"><Database size={14} />文件指纹</div>
            <dl className="meta-list">
              <div><dt>来源</dt><dd>{asset.source === "generated" ? "AI 生成" : "本地上传"}</dd></div>
              <div><dt>尺寸</dt><dd>{asset.width} × {asset.height}</dd></div>
              <div><dt>大小</dt><dd>{formatBytes(asset.size_bytes)}</dd></div>
              <div><dt>SHA-256</dt><dd className="mono break">{asset.sha256}</dd></div>
              <div><dt>创建时间</dt><dd>{formatDate(asset.created_at)}</dd></div>
              <div><dt>图床状态</dt><dd><span className={`status-text ${asset.remote.status}`}><StatusIcon status={asset.remote.status} />{statusLabel(asset.remote.status)}</span></dd></div>
              <div><dt>同步更新时间</dt><dd>{formatDate(asset.remote.updated_at)}</dd></div>
            </dl>
            {asset.remote.last_error ? <p className="error-detail">{asset.remote.last_error}</p> : null}
          </section>
          {asset.generation ? <section className="drawer-section generation-trace">
            <div className="section-label"><WandSparkles size={14} />生成记录</div>
            <div className="trace-grid">
              <span>Provider<strong>{asset.generation.provider_name}</strong></span>
              <span>模型<strong>{asset.generation.model}</strong></span>
              <span>耗时<strong>{formatDuration(asset.generation.elapsed_ms)}</strong></span>
              <span>Task ID<strong className="mono">{asset.generation.upstream_task_id || "-"}</strong></span>
            </div>
            {asset.generation.reference_asset ? <div className="reference-trace"><img src={asset.generation.reference_asset.thumbnail_url} alt="参考图" /><div><span>参考图</span><strong>{asset.generation.reference_asset.original_filename}</strong></div></div> : null}
            <div className="prompt-quote"><span>原始 prompt</span><p>{asset.generation.prompt}</p></div>
            {asset.generation.parent_job_id ? <p className="parent-link"><ChevronRight size={14} />派生自任务 <code>{asset.generation.parent_job_id.slice(0, 8)}</code></p> : null}
            {asset.generation.events?.length ? <div className="event-list">{asset.generation.events.slice(-6).map((event) => <div key={event.id}><i /><span>{event.message}</span><time>{formatDate(event.created_at)}</time></div>)}</div> : null}
          </section> : <section className="drawer-section"><div className="section-label"><Info size={14} />没有生成记录</div><p className="muted-copy">这是一次本地上传，后续可以通过标题、备注和标签整理。</p></section>}
        </> : null}
      </aside>
    </div>
  );
}

function SettingsDrawer({ config, providers, onClose, setNotice }: { config?: Config; providers: Provider[]; onClose: () => void; setNotice: (value: string) => void }) {
  const queryClient = useQueryClient();
  const [mediaDir, setMediaDir] = useState(config?.media_dir || "");
  const saveMediaDir = useMutation({
    mutationFn: () => updateMediaDir(mediaDir),
    onSuccess: (result) => {
      setMediaDir(result.media_dir);
      setNotice(result.message);
      queryClient.invalidateQueries({ queryKey: ["config"] });
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : "无法更新图片目录"),
  });

  useEffect(() => {
    if (config?.media_dir) setMediaDir(config.media_dir);
  }, [config?.media_dir]);

  return (
    <div className="drawer-backdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
      <aside className="settings-drawer">
        <div className="drawer-head">
          <div><p className="kicker">LOCAL CONFIGURATION</p><h2>运行设置</h2></div>
          <button type="button" className="icon-button" onClick={onClose} aria-label="关闭"><X size={18} /></button>
        </div>
        <section className="settings-block storage-settings-block">
          <div className="section-label"><Database size={14} />本地数据</div>
          <dl className="settings-list">
            <div><dt>访问范围</dt><dd><span className="status-text succeeded"><CheckCircle2 size={13} />仅当前电脑</span></dd></div>
            <div><dt>数据目录</dt><dd className="mono break">{config?.data_dir || "读取中"}</dd></div>
            <div><dt>数据库</dt><dd className="mono break">{config?.database_path || "读取中"}</dd></div>
          </dl>
          <label className="input-label storage-path-label">
            图片存储目录
            <div className="storage-path-control"><FolderOpen size={15} /><input value={mediaDir} onChange={(event) => setMediaDir(event.target.value)} placeholder="输入绝对路径" /></div>
          </label>
          <p className="settings-help">保存后会迁移原图和缩略图，数据库位置不变。迁移前请先停止 worker。</p>
          <button type="button" className="button primary full" onClick={() => saveMediaDir.mutate()} disabled={saveMediaDir.isPending || !mediaDir.trim()}><FolderOpen size={15} />{saveMediaDir.isPending ? "迁移中" : "保存图片路径"}</button>
          {saveMediaDir.error ? <div className="inline-error"><AlertTriangle size={15} />{saveMediaDir.error instanceof Error ? saveMediaDir.error.message : "无法更新图片目录"}</div> : null}
        </section>
        <section className="settings-block"><div className="section-label"><WandSparkles size={14} />生图 Provider</div><div className="provider-list">{providers.map((provider) => <div key={provider.id}><span className={provider.ready ? "provider-dot ready" : "provider-dot"} /><div><strong>{provider.name}</strong><small className="mono">{provider.base_url || "未配置 Base URL"}</small></div><em>{provider.ready ? "READY" : "BLOCKED"}</em></div>)}</div></section>
        <section className="settings-block"><div className="section-label"><CloudUpload size={14} />CloudFlare-ImgBed</div><dl className="settings-list"><div><dt>状态</dt><dd><span className={`status-text ${config?.imgbed.configured ? "succeeded" : "disabled"}`}>{config?.imgbed.configured ? <CheckCircle2 size={13} /> : <CircleDot size={13} />}{config?.imgbed.configured ? "已配置" : "未配置"}</span></dd></div><div><dt>REST 地址</dt><dd className="mono break">{config?.imgbed.base_url || "-"}</dd></div><div><dt>重试策略</dt><dd>仅手动重新同步</dd></div></dl></section>
        <p className="settings-foot">密钥只由本机后端读取，不会发送到浏览器。</p>
      </aside>
    </div>
  );
}

function RuntimeView({ runtime, setNotice }: { runtime?: RuntimeStatus; setNotice: (value: string) => void }) {
  const queryClient = useQueryClient();
  const start = useMutation({
    mutationFn: startWorker,
    onSuccess: (result) => {
      setNotice(result.started ? "worker 启动请求已发送" : result.message || "已有 worker 正在运行");
      queryClient.invalidateQueries({ queryKey: ["runtime"] });
    },
  });
  const stop = useMutation({
    mutationFn: stopWorker,
    onSuccess: () => {
      setNotice("已请求停止 worker");
      queryClient.invalidateQueries({ queryKey: ["runtime"] });
    },
  });
  const workers = runtime?.workers || [];
  return <div className="view-stack runtime-view">
    <div className="view-heading"><div><p className="kicker">RUNTIME CONTROL</p><h1>运行状态</h1><p className="view-subtitle">API 与后台 worker 的本机运行状态。</p></div><div className={`ready-badge ${runtime?.active_worker_count ? "ready" : "blocked"}`}><span />{runtime?.active_worker_count || 0} 个 worker</div></div>
    <div className="runtime-toolbar"><div className="runtime-summary"><span><Server size={15} />API <strong>运行中</strong></span><span><Activity size={15} />活跃 worker <strong>{runtime?.active_worker_count ?? "-"}</strong></span></div><button type="button" className="button primary" onClick={() => start.mutate()} disabled={start.isPending || Boolean(runtime?.active_worker_count)}><Play size={15} />启动 worker</button></div>
    <section className="runtime-panel panel"><div className="panel-heading compact"><span className="panel-number">01</span><div><p className="kicker">PROCESSES</p><h2>进程列表</h2></div></div><div className="runtime-list"><div className="runtime-row api"><div className="runtime-icon"><Server size={17} /></div><div className="runtime-main"><strong>FrameLab API</strong><span>PID {runtime?.api.pid ?? "-"}</span></div><span className="runtime-status running"><i />运行中</span></div>{workers.length ? workers.map((worker) => <div className="runtime-row" key={`${worker.pid}-${worker.started_at}`}><div className="runtime-icon"><Activity size={17} /></div><div className="runtime-main"><strong>FrameLab worker</strong><span>PID {worker.pid} · 启动于 {formatDate(worker.started_at)}</span><small>最后心跳 {formatDate(worker.heartbeat_at)}</small></div><span className={`runtime-status ${worker.status}`}><i />{worker.status === "running" ? "运行中" : "已停止"}</span>{worker.status === "running" ? <button type="button" className="icon-button small runtime-stop" onClick={() => stop.mutate(worker.pid)} disabled={stop.isPending} title="停止 worker" aria-label={`停止 worker ${worker.pid}`}><Square size={14} /></button> : null}</div>) : <div className="runtime-empty"><Activity size={22} /><span>暂无 worker 运行记录</span></div>}</div></section>
  </div>;
}

function GalleryView({ assets, filters, setFilters, selected, setSelected, onOpen, onUpload, onGenerate, setNotice }: { assets: Asset[]; filters: { q: string; source: string; sync_status: string }; setFilters: (value: { q: string; source: string; sync_status: string }) => void; selected: Set<string>; setSelected: (value: Set<string>) => void; onOpen: (id: string) => void; onUpload: () => void; onGenerate: () => void; setNotice: (value: string) => void }) {
  const queryClient = useQueryClient();
  const [tagInput, setTagInput] = useState("");
  const sync = useMutation({ mutationFn: () => bulkSync([...selected]), onSuccess: (result) => { setNotice(`${result.queued} 张图片已加入同步队列`); setSelected(new Set()); queryClient.invalidateQueries({ queryKey: ["assets"] }); queryClient.invalidateQueries({ queryKey: ["stats"] }); } });
  const tag = useMutation({ mutationFn: () => bulkTags([...selected], tagInput.split(",").map((item) => item.trim()).filter(Boolean)), onSuccess: () => { setNotice("批量标签已保存"); setTagInput(""); setSelected(new Set()); queryClient.invalidateQueries({ queryKey: ["assets"] }); queryClient.invalidateQueries({ queryKey: ["tags"] }); } });
  const remove = useMutation({
    mutationFn: () => bulkDelete([...selected]),
    onSuccess: (result) => {
      setNotice(result.deleted ? `已删除 ${result.deleted} 张图片` : "没有可删除的图片");
      setSelected(new Set());
      queryClient.invalidateQueries({ queryKey: ["assets"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : "批量删除失败"),
  });
  const handleBatchDelete = () => {
    if (!selected.size || remove.isPending) return;
    if (window.confirm(`确定删除选中的 ${selected.size} 张图片？删除后无法恢复。`)) remove.mutate();
  };
  const allSelected = assets.length > 0 && assets.every((asset) => selected.has(asset.id));
  const toggleAll = () => setSelected(allSelected ? new Set() : new Set(assets.map((asset) => asset.id)));
  return <div className="view-stack">
    <div className="view-heading"><div><p className="kicker">PERSONAL AI LIBRARY</p><h1>图库</h1><p className="view-subtitle">把每次创作变成可检索、可复盘的资产。</p></div><button type="button" className="button primary" onClick={onUpload}><Upload size={16} />上传图片</button></div>
    <div className="filter-bar">
      <div className="search-box"><Search size={17} /><input value={filters.q} onChange={(event) => setFilters({ ...filters, q: event.target.value })} placeholder="搜索文件名、标题、备注或 prompt" /></div>
      <div className="filter-select"><Filter size={15} /><select value={filters.source} onChange={(event) => setFilters({ ...filters, source: event.target.value })}><option value="all">全部来源</option><option value="generated">AI 生成</option><option value="upload">本地上传</option></select></div>
      <div className="filter-select"><CloudUpload size={15} /><select value={filters.sync_status} onChange={(event) => setFilters({ ...filters, sync_status: event.target.value })}><option value="all">全部同步状态</option><option value="succeeded">已同步</option><option value="pending">待同步</option><option value="failed">同步失败</option><option value="disabled">仅本地</option></select></div>
    </div>
    {selected.size ? <div className="batch-bar"><span><Check size={15} />已选择 {selected.size} 张</span><div className="batch-actions"><div className="batch-tag-input"><TagIcon size={14} /><input value={tagInput} onChange={(event) => setTagInput(event.target.value)} placeholder="标签，逗号分隔" /></div><button type="button" className="button small quiet" onClick={() => tag.mutate()} disabled={!tagInput.trim() || tag.isPending}><TagIcon size={14} />打标签</button><button type="button" className="button small quiet" onClick={() => sync.mutate()} disabled={sync.isPending}><CloudUpload size={14} />同步图床</button><span className="batch-divider" /><button type="button" className="button small danger" onClick={handleBatchDelete} disabled={remove.isPending}><Trash2 size={14} />{remove.isPending ? "删除中" : "删除"}</button><button type="button" className="icon-button small" onClick={() => setSelected(new Set())} aria-label="取消选择"><X size={15} /></button></div></div> : <div className="selection-hint"><button type="button" onClick={toggleAll}>{allSelected ? "取消全选" : "选择当前页"}</button><span>点击图片查看详情，选择后可批量整理</span></div>}
    {assets.length ? <div className="asset-grid">{assets.map((asset) => <AssetCard key={asset.id} asset={asset} selected={selected.has(asset.id)} onSelect={() => { const next = new Set(selected); next.has(asset.id) ? next.delete(asset.id) : next.add(asset.id); setSelected(next); }} onOpen={() => onOpen(asset.id)} />)}</div> : <EmptyGallery onGenerate={onGenerate} onUpload={onUpload} />}
  </div>;
}

function useGenerateDraftState<T>(key: string, initialValue: T) {
  const storageKey = `framelab.generate.${key}`;
  const [value, setValue] = useState<T>(() => {
    try {
      const stored = sessionStorage.getItem(storageKey);
      return stored === null ? initialValue : JSON.parse(stored) as T;
    } catch {
      return initialValue;
    }
  });
  useEffect(() => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(value));
    } catch {
      // Storage may be unavailable in private or restricted browser contexts.
    }
  }, [storageKey, value]);
  return [value, setValue] as const;
}

const DEFAULT_GENERATE_PROMPT = "把参考图美化成一张更精致的作品，保留主体和核心构图，优化光线、材质、色彩与细节，画面干净，无 logo 和水印。";

function GenerateView({ config, providers, onCreated, setNotice }: { config?: Config; providers: Provider[]; onCreated: () => void; setNotice: (value: string) => void }) {
  const [prompt, setPrompt] = useGenerateDraftState("prompt", DEFAULT_GENERATE_PROMPT);
  const [model, setModel] = useGenerateDraftState("model", "gpt-image-2");
  const [size, setSize] = useGenerateDraftState("size", "auto");
  const [quality, setQuality] = useGenerateDraftState("quality", "high");
  const [timeout, setTimeoutValue] = useGenerateDraftState("timeout", 600);
  const [providerId, setProviderId] = useGenerateDraftState("provider", "");
  const [endpoint, setEndpoint] = useGenerateDraftState("endpoint", "");
  const [extraParamsText, setExtraParamsText] = useGenerateDraftState("extra-params", "");
  const [syncEnabled, setSyncEnabled] = useGenerateDraftState("sync-enabled", true);
  const [parentJobId, setParentJobId] = useGenerateDraftState("parent-job", "");
  const [referenceAsset, setReferenceAsset] = useGenerateDraftState<Asset | null>("reference-asset", null);
  const [referencePickerOpen, setReferencePickerOpen] = useState(false);
  const referenceFileInput = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const chosenProvider = providers.find((item) => item.id === (providerId || config?.provider.id));
  const defaultEndpoint = defaultGenerationEndpoint(chosenProvider?.base_url || config?.provider.base_url, Boolean(referenceAsset));
  const automaticEndpoints = [
    defaultGenerationEndpoint(chosenProvider?.base_url || config?.provider.base_url, false),
    defaultGenerationEndpoint(chosenProvider?.base_url || config?.provider.base_url, true),
  ];
  const endpointOverride = automaticEndpoints.includes(endpoint.trim()) ? "" : endpoint.trim();
  const clearAutomaticEndpointOverride = (hasReference: boolean) => {
    const currentEndpoint = endpoint.trim();
    const baseUrl = chosenProvider?.base_url || config?.provider.base_url;
    if (currentEndpoint && currentEndpoint === defaultGenerationEndpoint(baseUrl, !hasReference)) setEndpoint("");
  };
  const referenceUpload = useMutation({
    mutationFn: (file: File) => uploadAsset(file, { title: "参考图", notes: "", tags: "参考图", sync_enabled: false }),
    onSuccess: (result) => {
      clearAutomaticEndpointOverride(true);
      setReferenceAsset(result.asset);
      queryClient.invalidateQueries({ queryKey: ["assets"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
      setNotice(result.created ? "参考图已上传并加入图库" : "已使用图库中的同一张图片作为参考图");
    },
    onError: (error) => setNotice(error instanceof Error ? error.message : "参考图上传失败"),
  });
  const extraParamsResult = parseExtraParams(extraParamsText);
  const mutation = useMutation({
    mutationFn: () => createJob({ prompt, model, size: size.trim().toLowerCase(), quality, timeout, endpoint: endpointOverride || undefined, extra_params: extraParamsResult.params, provider_id: providerId || undefined, parent_job_id: parentJobId || undefined, reference_asset_id: referenceAsset?.id || undefined, sync_enabled: syncEnabled }),
    onSuccess: (job) => { setNotice(`任务 ${job.id.slice(0, 8)} 已加入队列`); onCreated(); },
  });
  const effectiveEndpoint = endpointOverride || defaultEndpoint;
  const displayedEndpoint = effectiveEndpoint;
  const requestPreview = useMemo(() => ({
    method: "POST",
    endpoint: effectiveEndpoint || "未配置",
    model,
    prompt: "...",
    n: 1,
    size,
    quality,
    response_format: "url",
    ...extraParamsResult.params,
    ...(referenceAsset ? { "image[]": { filename: referenceAsset.original_filename, mime_type: referenceAsset.mime_type, size_bytes: referenceAsset.size_bytes } } : {}),
  }), [effectiveEndpoint, extraParamsResult.params, model, prompt, quality, referenceAsset, size]);
  const copyRequest = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(requestPreview, null, 2));
      setNotice("请求预览已复制");
    } catch {
      setNotice("浏览器不允许复制，请直接选中预览内容");
    }
  };
  const handleReferenceFile = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (file) referenceUpload.mutate(file);
    event.target.value = "";
  };
  const removeReference = () => {
    clearAutomaticEndpointOverride(false);
    setReferenceAsset(null);
    if (referenceFileInput.current) referenceFileInput.current.value = "";
  };
  const selectReference = (asset: Asset) => {
    clearAutomaticEndpointOverride(true);
    setReferenceAsset(asset);
    setReferencePickerOpen(false);
    setNotice("已选择图库图片作为参考图");
  };
  const resetRequestDefaults = () => {
    setProviderId("");
    setEndpoint("");
    setModel("gpt-image-2");
    setSize("auto");
    setQuality("high");
    setTimeoutValue(600);
    setExtraParamsText("");
    setNotice("请求参数已恢复默认");
  };
  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    if (!prompt.trim()) return;
    if (referenceUpload.isPending) { setNotice("参考图仍在上传，请稍候"); return; }
    if (timeout < 30 || timeout > 1800) { setNotice("最长等待必须在 30 到 1800 秒之间"); return; }
    if (endpointError(displayedEndpoint)) { setNotice(endpointError(displayedEndpoint)); return; }
    if (sizeError(size)) { setNotice(sizeError(size)); return; }
    if (extraParamsResult.error) { setNotice(extraParamsResult.error); return; }
    mutation.mutate();
  };
  return (
    <div className="view-stack generate-view">
      <div className="view-heading"><div><p className="kicker">GENERATION DESK</p><h1>开始创作</h1><p className="view-subtitle">上传参考图，再用 prompt 修改、优化或重绘；每个参数都会成为回溯记录。</p></div><div className={`ready-badge ${config?.ready ? "ready" : "blocked"}`}><span />{config?.ready ? "Provider ready" : "等待配置"}</div></div>
      {!config?.ready ? <div className="notice-card warning"><AlertTriangle size={18} /><div><strong>生图 provider 尚未准备好</strong><p>请在项目根目录的 .env 中配置 CODEX_IMAGE_API_KEY 和 CODEX_IMAGE_BASE_URL，然后重启服务。</p></div></div> : null}
      <form className="generation-layout" onSubmit={submit}>
        <section className="generation-form panel">
          <div className="panel-heading"><span className="panel-number">01</span><div><p className="kicker">PROMPT + REFERENCE</p><h2>创作指令</h2></div><span className="panel-mark"><WandSparkles size={17} /></span></div>
          <section className="reference-image-section">
            <div className="reference-section-head">
              <div><div className="section-label"><ImagePlus size={14} />参考图 <em>可选</em></div><p className="reference-copy">有参考图时会调用 edits 接口；没有参考图则按纯 prompt 生图。</p></div>
              <input ref={referenceFileInput} type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden onChange={handleReferenceFile} />
              <div className="reference-actions">
                <button type="button" className="button small quiet" onClick={() => setReferencePickerOpen(true)} disabled={referenceUpload.isPending}><Images size={14} />从图库选择</button>
                <button type="button" className="button small quiet" onClick={() => referenceFileInput.current?.click()} disabled={referenceUpload.isPending}><Upload size={14} />{referenceUpload.isPending ? "上传中" : referenceAsset ? "更换参考图" : "上传参考图"}</button>
              </div>
            </div>
            {referenceAsset ? <div className="reference-preview"><img src={referenceAsset.preview_url} alt={referenceAsset.title || referenceAsset.original_filename} /><div className="reference-meta"><strong>{referenceAsset.title || referenceAsset.original_filename}</strong><span>{referenceAsset.width && referenceAsset.height ? `${referenceAsset.width} × ${referenceAsset.height}` : "图片"} · {referenceAsset.source === "upload" ? "图库原图" : "生成图原图"} · 任务提交时按原图发送</span><button type="button" className="text-button" onClick={removeReference}><X size={13} />移除参考图</button></div></div> : <div className="reference-empty-actions"><button type="button" className="reference-dropzone" onClick={() => referenceFileInput.current?.click()}><ImagePlus size={24} /><strong>上传一张新参考图</strong><span>PNG、JPEG、WebP 或 GIF，异步改图请求上限 15 MB</span></button><button type="button" className="reference-library-dropzone" onClick={() => setReferencePickerOpen(true)}><Images size={24} /><strong>使用图库中的图片</strong><span>从已上传图片中选择，不需要重复上传</span></button></div>}
            {referenceUpload.error ? <div className="inline-error"><AlertTriangle size={15} />{referenceUpload.error instanceof Error ? referenceUpload.error.message : "参考图上传失败"}</div> : null}
          </section>
          <label className="input-label large">Prompt<textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} maxLength={32000} rows={15} placeholder="描述你希望如何修改参考图..." /><span className="field-foot"><span>{prompt.length.toLocaleString()} / 32,000</span><span>原文将永久保留</span></span></label>
          <label className="input-label">父任务 ID <div className="input-with-icon"><Copy size={15} /><input value={parentJobId} onChange={(event) => setParentJobId(event.target.value)} placeholder="可选：从历史任务继续生成" /></div></label>
          <div className="form-footer"><label className="toggle-row"><input type="checkbox" checked={syncEnabled} onChange={(event) => setSyncEnabled(event.target.checked)} /><span className="toggle-visual" /><span>完成后自动同步到 cloudflare-imgbed</span></label><button type="submit" className="button primary submit-button" disabled={mutation.isPending || referenceUpload.isPending || !config?.ready}><WandSparkles size={17} />{mutation.isPending ? "已提交，等待响应" : referenceAsset ? "提交参考图修改" : "加入生成队列"}<ArrowUpRight size={16} /></button></div>
          {mutation.error ? <div className="inline-error"><AlertTriangle size={15} />{mutation.error.message}</div> : null}
        </section>
        <aside className="generation-options panel">
          <div className="panel-heading compact"><span className="panel-number">02</span><div><p className="kicker">REQUEST SNAPSHOT</p><h2>请求参数</h2></div><button type="button" className="button small quiet reset-button" onClick={resetRequestDefaults} title="恢复请求参数默认值"><RefreshCw size={13} />恢复默认</button></div>
          <label className="input-label">Provider<select value={providerId} onChange={(event) => setProviderId(event.target.value)}>{providers.map((provider) => <option value={provider.id} key={provider.id}>{provider.name}{provider.ready ? " · ready" : " · 未配置"}</option>)}</select></label>
          <label className="input-label">调用地址<div className="endpoint-field"><b>POST</b><input value={displayedEndpoint} onChange={(event) => setEndpoint(event.target.value)} placeholder="Provider 未配置 async 地址" spellCheck={false} /></div></label>
          <p className="field-help">默认显示当前模式的 async 地址；手动修改后只覆盖本次任务。</p>
          {endpointError(displayedEndpoint) ? <div className="inline-error compact-error"><AlertTriangle size={15} />{endpointError(displayedEndpoint)}</div> : null}
          <div className="option-grid"><label className="input-label">模型<select value={model} onChange={(event) => setModel(event.target.value)}><option value="gpt-image-2">gpt-image-2</option><option value="gpt-image-2-2k">gpt-image-2-2k</option><option value="gpt-image-2-4k">gpt-image-2-4k</option></select></label><label className="input-label">尺寸<div className="size-field"><input list="image-size-suggestions" value={size} onChange={(event) => setSize(event.target.value)} placeholder="例如 1200x800" spellCheck={false} /><datalist id="image-size-suggestions"><option value="auto" /><option value="1024x1024" /><option value="1536x864" /><option value="864x1536" /><option value="2048x2048" /><option value="2560x1440" /><option value="1440x2560" /><option value="3840x2160" /><option value="2160x3840" /><option value="2880x2880" /></datalist></div></label></div>
          {sizeError(size) ? <div className="inline-error compact-error"><AlertTriangle size={15} />{sizeError(size)}</div> : null}
          <fieldset className="quality-options"><legend>质量</legend><div>{["low", "medium", "high", "auto"].map((item) => <label key={item}><input type="radio" name="quality" value={item} checked={quality === item} onChange={() => setQuality(item)} /><span>{item === "medium" ? "MED" : item.toUpperCase()}</span></label>)}</div></fieldset>
          <label className="input-label">最长等待<span className="unit-field"><input type="number" min={30} max={1800} step={30} value={timeout} onChange={(event) => setTimeoutValue(Number(event.target.value))} /><b>SEC</b></span></label>
          <label className="input-label extra-params-label">附加参数<textarea value={extraParamsText} onChange={(event) => setExtraParamsText(event.target.value)} rows={7} spellCheck={false} placeholder={'{\n  "async": true\n}'} /></label>
          <p className="field-help">仅填写 provider body 参数。模型、prompt、n、尺寸、质量和 response_format 由上方控件生成。</p>
          {extraParamsResult.error ? <div className="inline-error compact-error"><AlertTriangle size={15} />{extraParamsResult.error}</div> : null}
          <div className="request-summary"><div><span>调用方式</span><strong><span className="status-live" />{referenceAsset ? "ASYNC EDIT" : "ASYNC JOB"}</strong></div><div><span>Provider</span><strong>{chosenProvider?.base_url || "未配置"}</strong></div><div><span>参考图</span><strong>{referenceAsset ? "1 张 · image[]" : "未选择"}</strong></div><div><span>自动重试</span><strong>0 次</strong></div></div>
          <section className="request-preview" aria-label="完整请求预览">
            <div className="request-preview-head"><div><span className="section-label"><Terminal size={14} />完整请求</span><small>prompt 仅在预览中省略，实际发送完整内容</small></div><button type="button" className="icon-button small" onClick={copyRequest} title="复制请求预览" aria-label="复制请求预览"><Copy size={14} /></button></div>
            <pre>{JSON.stringify(requestPreview, null, 2)}</pre>
          </section>
        </aside>
      </form>
      {referencePickerOpen ? <ReferenceAssetPicker onClose={() => setReferencePickerOpen(false)} onSelect={selectReference} /> : null}
    </div>
  );
}

function ReferenceAssetPicker({ onClose, onSelect }: { onClose: () => void; onSelect: (asset: Asset) => void }) {
  const [query, setQuery] = useState("");
  const assets = useQuery({
    queryKey: ["reference-assets", query],
    queryFn: () => getAssets({ q: query, source: "upload", page: 1, page_size: 100 }),
  });

  return <div className="drawer-backdrop" onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
    <aside className="reference-picker-drawer" role="dialog" aria-modal="true" aria-labelledby="reference-picker-title">
      <div className="drawer-head">
        <div><p className="kicker">LOCAL ASSET LIBRARY</p><h2 id="reference-picker-title">选择图库参考图</h2></div>
        <button type="button" className="icon-button" onClick={onClose} aria-label="关闭"><X size={18} /></button>
      </div>
      <div className="reference-picker-toolbar"><div className="search-box"><Search size={16} /><input autoFocus value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索文件名、标题或备注" /></div><span>{assets.data ? `${assets.data.total} 张已上传图片` : "读取中"}</span></div>
      {assets.isLoading ? <div className="reference-picker-empty"><LoaderCircle className="spin" /><span>读取图库...</span></div> : null}
      {assets.error ? <div className="inline-error"><AlertTriangle size={15} />无法读取图库，请稍后重试</div> : null}
      {assets.data && !assets.data.items.length ? <div className="reference-picker-empty"><Images size={28} /><strong>{query ? "没有匹配的上传图片" : "还没有上传图片"}</strong><span>{query ? "换一个关键词试试。" : "先上传一张图片，它会出现在这里。"}</span></div> : null}
      {assets.data?.items.length ? <div className="reference-picker-grid">{assets.data.items.map((asset) => <button type="button" className="reference-picker-card" key={asset.id} onClick={() => onSelect(asset)}><span className="reference-picker-image"><img src={asset.thumbnail_url} alt="" /></span><span className="reference-picker-card-meta"><strong>{asset.title || asset.original_filename}</strong><span>{asset.width && asset.height ? `${asset.width} × ${asset.height}` : "图片"} · {formatDate(asset.created_at)}</span></span><span className="reference-picker-select">选择</span></button>)}</div> : null}
    </aside>
  </div>;
}

function JobLogPanel({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  const detail = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => getJob(jobId),
    refetchInterval: (query) => {
      const job = query.state.data as Job | undefined;
      return job && !["succeeded", "failed", "canceled"].includes(job.status) ? 1500 : false;
    },
  });
  const job = detail.data;

  return (
    <section className="job-log-panel">
      <div className="job-log-head">
        <div><p className="kicker">TASK LOG</p><h3>任务日志 <span className="mono">{jobId.slice(0, 8)}</span></h3></div>
        <button type="button" className="icon-button small" onClick={onClose} aria-label="关闭日志"><X size={15} /></button>
      </div>
      {detail.isLoading ? <div className="job-log-empty"><LoaderCircle className="spin" /><span>读取任务日志...</span></div> : null}
      {detail.error ? <div className="inline-error"><AlertTriangle size={15} />无法读取任务日志</div> : null}
      {job ? <>
        <div className="job-log-summary">
          <span><b>状态</b>{statusLabel(job.status)}</span>
          <span><b>Provider</b>{job.provider_name}</span>
          <span><b>Task ID</b><code>{job.upstream_task_id || "-"}</code></span>
          <span><b>耗时</b>{formatDuration(job.elapsed_ms)}</span>
        </div>
        {job.error_message ? <p className="error-detail">{job.error_message}</p> : null}
        <div className="job-event-list">
          {job.events?.length ? job.events.map((event) => {
            const hasPayload = Object.keys(event.payload || {}).length > 0;
            return <article className="job-event" key={event.id}>
              <div className="job-event-head"><span className={`job-event-type ${event.type}`}>{event.type}</span><time>{formatDate(event.created_at)}</time></div>
              <p>{event.message || "-"}</p>
              {hasPayload ? <pre>{JSON.stringify(event.payload, null, 2)}</pre> : null}
            </article>;
          }) : <p className="muted-copy">暂无事件记录</p>}
        </div>
        <details className="job-log-data" open><summary>请求快照（prompt 已省略）</summary><pre>{JSON.stringify(requestForDisplay(job.request), null, 2)}</pre></details>
        <details className="job-log-data"><summary>最后响应</summary><pre>{JSON.stringify(job.response || {}, null, 2)}</pre></details>
      </> : null}
    </section>
  );
}

const RESERVED_REQUEST_KEYS = ["model", "prompt", "n", "size", "quality", "response_format", "method", "endpoint", "image[]"];

function defaultGenerationEndpoint(baseUrl: string | undefined, hasReference: boolean) {
  const base = (baseUrl || "").replace(/\/+$/, "");
  if (!base) return "";
  const root = base.endsWith("/images/generations") || base.endsWith("/images/edits")
    ? base.replace(/\/images\/(generations|edits)$/, "")
    : base.endsWith("/v1") ? base : `${base}/v1`;
  return `${root}/images/${hasReference ? "edits" : "generations"}/async`;
}

function parseExtraParams(value: string): { params: Record<string, unknown>; error: string } {
  if (!value.trim()) return { params: {}, error: "" };
  try {
    const decoded: unknown = JSON.parse(value);
    if (!decoded || Array.isArray(decoded) || typeof decoded !== "object") {
      return { params: {}, error: "附加参数必须是 JSON 对象，例如 {\"async\": true}。" };
    }
    const reserved = Object.keys(decoded).filter((key) => RESERVED_REQUEST_KEYS.includes(key));
    if (reserved.length) return { params: {}, error: `附加参数不能覆盖表单字段：${reserved.join(", ")}。` };
    return { params: decoded as Record<string, unknown>, error: "" };
  } catch {
    return { params: {}, error: "附加参数不是有效 JSON，请检查括号、引号和逗号。" };
  }
}

function endpointError(value: string) {
  if (!value.trim()) return "";
  try {
    const parsed = new URL(value.trim());
    if (!(parsed.protocol === "http:" || parsed.protocol === "https:")) return "调用地址必须使用 http 或 https。";
    if (parsed.username || parsed.password || parsed.hash) return "调用地址不能包含账号、密码或片段。";
    return "";
  } catch {
    return "调用地址必须是完整的 http 或 https URL。";
  }
}

function sizeError(value: string) {
  const normalized = value.trim().toLowerCase();
  if (normalized === "auto") return "";
  if (!/^[1-9]\d{0,4}x[1-9]\d{0,4}$/.test(normalized)) return "尺寸请输入 auto 或正整数宽x高，例如 1200x800。";
  const [width, height] = normalized.split("x").map(Number);
  return width > 0 && height > 0 ? "" : "尺寸的宽度和高度必须大于 0。";
}

function requestForDisplay(request: Record<string, unknown> | undefined) {
  if (!request) return {};
  return Object.prototype.hasOwnProperty.call(request, "prompt") ? { ...request, prompt: "..." } : request;
}

type JobFilter = "all" | "active" | "succeeded" | "failed" | "canceled";

function JobsView({ jobs, onOpenAsset, setNotice }: { jobs: Job[]; onOpenAsset: (id: string) => void; setNotice: (value: string) => void }) {
  const queryClient = useQueryClient();
  const [logJobId, setLogJobId] = useState<string | null>(null);
  const [jobFilter, setJobFilter] = useState<JobFilter>("all");
  const copyJobId = async (id: string) => {
    try {
      await navigator.clipboard.writeText(id);
      setNotice("Job ID 已复制");
    } catch {
      setNotice("浏览器不允许复制，请直接选中 Job ID");
    }
  };
  const retry = useMutation({ mutationFn: (id: string) => retryJob(id), onSuccess: (job) => { setNotice(`已创建手动重试任务 ${job.id.slice(0, 8)}`); queryClient.invalidateQueries({ queryKey: ["jobs"] }); queryClient.invalidateQueries({ queryKey: ["stats"] }); }, onError: (error) => setNotice(error instanceof Error ? error.message : "手动重试失败") });
  const cancel = useMutation({ mutationFn: (id: string) => cancelJob(id), onSuccess: (job) => { setNotice(`任务 ${job.id.slice(0, 8)} 已停止`); queryClient.invalidateQueries({ queryKey: ["jobs"] }); queryClient.invalidateQueries({ queryKey: ["job", job.id] }); queryClient.invalidateQueries({ queryKey: ["stats"] }); }, onError: (error) => setNotice(error instanceof Error ? error.message : "停止任务失败") });
  const remove = useMutation({ mutationFn: (id: string) => deleteJob(id), onSuccess: (result) => { setLogJobId(null); setNotice(`任务 ${result.id.slice(0, 8)} 已删除`); queryClient.removeQueries({ queryKey: ["job", result.id] }); queryClient.invalidateQueries({ queryKey: ["jobs"] }); queryClient.invalidateQueries({ queryKey: ["stats"] }); }, onError: (error) => setNotice(error instanceof Error ? error.message : "删除任务失败") });
  const active = jobs.filter((job) => !["succeeded", "failed", "canceled"].includes(job.status));
  const failed = jobs.filter((job) => job.status === "failed");
  const tabs: Array<{ key: JobFilter; label: string; count: number }> = [
    { key: "all", label: "全部", count: jobs.length },
    { key: "active", label: "进行中", count: active.length },
    { key: "succeeded", label: "已完成", count: jobs.filter((job) => job.status === "succeeded").length },
    { key: "failed", label: "失败", count: failed.length },
    { key: "canceled", label: "已取消", count: jobs.filter((job) => job.status === "canceled").length },
  ];
  const visibleJobs = jobFilter === "all" ? jobs : jobFilter === "active" ? active : jobs.filter((job) => job.status === jobFilter);
  return <div className="view-stack"><div className="view-heading"><div><p className="kicker">JOB CONTROL</p><h1>任务中心</h1><p className="view-subtitle">后台任务不会因为关闭浏览器而停止。</p><div className="job-tabs" role="tablist" aria-label="任务状态"><span className="job-tabs-label">筛选任务</span>{tabs.map((tab) => <button type="button" role="tab" aria-selected={jobFilter === tab.key} className={`job-tab ${jobFilter === tab.key ? "active" : ""}`} key={tab.key} onClick={() => { setJobFilter(tab.key); setLogJobId(null); }}><span>{tab.label}</span><b>{tab.count}</b></button>)}</div></div><div className="job-summary"><span><i className="blue-dot" />{active.length} 处理中</span><span><i className="red-dot" />{failed.length} 失败</span></div></div>{visibleJobs.length ? <div className="job-list">{visibleJobs.map((job) => {
    const isActive = !["succeeded", "failed", "canceled"].includes(job.status);
    return <article className={`job-row ${job.status}`} key={job.id}>
      <div className="job-status"><StatusIcon status={job.status} /><span>{statusLabel(job.status)}</span></div>
      <div className="job-main"><div className="job-title"><strong>{job.prompt.slice(0, 140)}</strong></div><div className="job-id-row"><span>Job ID</span><code title={job.id}>{job.id}</code><button type="button" className="icon-button small" onClick={() => copyJobId(job.id)} title="复制 Job ID" aria-label={`复制 Job ID ${job.id}`}><Copy size={13} /></button></div><p>{job.progress_message}</p><div className="job-meta"><span><WandSparkles size={13} />{job.provider_name}</span><span><Timer size={13} />{formatDuration(job.elapsed_ms)}</span><span>{job.model}</span>{job.reference_asset_id ? <span><ImagePlus size={13} />参考图</span> : null}<span>{formatDate(job.created_at)}</span></div></div>
      <div className="job-actions">
        {job.asset_deleted ? <span className="job-deleted-hint" title="对应图片已从图库删除"><Trash2 size={14} />图片已删除</span> : job.asset_id ? <button type="button" className="button small quiet" onClick={() => onOpenAsset(job.asset_id!)}><FileImage size={14} />查看图片</button> : null}
        <button type="button" className="button small quiet" onClick={() => setLogJobId(logJobId === job.id ? null : job.id)}><Activity size={14} />{logJobId === job.id ? "收起日志" : "查看日志"}</button>
        {isActive ? <button type="button" className="button small danger" onClick={() => { if (window.confirm("确定停止这个生成任务？已提交到上游的任务可能仍会继续消耗上游资源。")) cancel.mutate(job.id); }} disabled={cancel.isPending}><Square size={14} />停止</button> : null}
        {job.status === "failed" ? <button type="button" className="button small quiet" onClick={() => retry.mutate(job.id)} disabled={retry.isPending}><RefreshCw size={14} />手动重试</button> : null}
        {!isActive ? <button type="button" className="button small danger" onClick={() => { if (window.confirm("确定删除这条任务记录？对应图片不会删除。")) remove.mutate(job.id); }} disabled={remove.isPending}><Trash2 size={14} />删除</button> : null}
      </div>
      {logJobId === job.id ? <JobLogPanel jobId={job.id} onClose={() => setLogJobId(null)} /> : null}
    </article>;
  })}</div> : <div className="job-list"><div className="empty-panel"><ListTodo size={30} /><strong>{jobs.length ? `${statusLabel(jobFilter === "active" ? "running" : jobFilter)}任务为空` : "还没有生成任务"}</strong><span>{jobs.length ? "切换其他状态标签查看任务。" : "去“开始创作”提交第一条 prompt。"}</span></div></div>}</div>;
}

export default function App() {
  const [view, setView] = useState<View>("gallery");
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState({ q: "", source: "all", sync_status: "all" });
  const [notice, setNotice] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const config = useQuery({ queryKey: ["config"], queryFn: getConfig, refetchInterval: 10000 });
  const stats = useQuery({ queryKey: ["stats"], queryFn: getStats, refetchInterval: 5000 });
  const providers = useQuery({ queryKey: ["providers"], queryFn: getProviders });
  const tags = useQuery({ queryKey: ["tags"], queryFn: getTags });
  const jobs = useQuery({ queryKey: ["jobs"], queryFn: getJobs, refetchInterval: 2000 });
  const assets = useQuery({
    queryKey: ["assets", filters],
    queryFn: () => getAssets(filters),
    refetchInterval: (query) => {
      const current = query.state.data as AssetPage | undefined;
      const syncInProgress = current?.items.some((asset) => isSyncInProgress(asset.remote.status)) ?? false;
      const generationInProgress = (jobs.data || []).some(
        (job) => !["succeeded", "failed", "canceled"].includes(job.status),
      );
      // 2s 快轮询用于同步/生成进行中；空闲时也保留 10s 兜底轮询，
      // 避免上传（尤其是 sync_enabled=false 的参考图）后列表一直停留在旧数据，
      // 而顶部数量（stats 5s 轮询）已经更新。
      return syncInProgress || generationInProgress ? 2000 : 10000;
    },
  });
  const runtime = useQuery({ queryKey: ["runtime"], queryFn: getRuntime, refetchInterval: 2000 });
  const succeededJobIds = useRef<Set<string>>(new Set());
  useEffect(() => {
    const jobsList = jobs.data || [];
    const newlySucceeded = jobsList.some((job) => job.status === "succeeded" && !succeededJobIds.current.has(job.id));
    if (newlySucceeded) {
      queryClient.invalidateQueries({ queryKey: ["assets"] });
      queryClient.invalidateQueries({ queryKey: ["stats"] });
    }
    succeededJobIds.current = new Set(jobsList.map((job) => job.id));
  }, [jobs.data, queryClient]);
  const upload = useMutation({ mutationFn: (file: File) => uploadAsset(file, { title: "", notes: "", tags: "", sync_enabled: true }), onSuccess: (result) => { setNotice(result.created ? "图片已入库并进入同步队列" : "检测到相同文件，已打开已有记录"); queryClient.invalidateQueries({ queryKey: ["assets"] }); queryClient.invalidateQueries({ queryKey: ["stats"] }); setSelectedAssetId(result.asset.id); }, onError: (error) => setNotice(error.message) });
  useEffect(() => { if (!notice) return; const timer = window.setTimeout(() => setNotice(""), 4200); return () => window.clearTimeout(timer); }, [notice]);
  const activeJobCount = useMemo(() => (jobs.data || []).filter((job) => !["succeeded", "failed", "canceled"].includes(job.status)).length, [jobs.data]);
  const chooseView = (next: View) => { setView(next); setSelected(new Set()); if (next === "gallery") { queryClient.invalidateQueries({ queryKey: ["assets"] }); } };
  const openUpload = () => fileInput.current?.click();
  const handleFile = (event: React.ChangeEvent<HTMLInputElement>) => { const file = event.target.files?.[0]; if (file) upload.mutate(file); event.target.value = ""; };
  const statData: Stats = stats.data || { assets: 0, generated: 0, uploaded: 0, active_jobs: 0, failed_jobs: 0, synced: 0 };
  return <div className="app-shell"><header className="topbar"><div className="brand"><div className="brand-icon"><Sparkles size={19} /><span /></div><div><strong>FrameLab</strong><small>PERSONAL AI ARCHIVE</small></div></div><nav className="main-nav"><NavButton active={view === "gallery"} icon={<LayoutGrid size={16} />} label="图库" onClick={() => chooseView("gallery")} count={statData.assets} /><NavButton active={view === "generate"} icon={<WandSparkles size={16} />} label="开始创作" onClick={() => chooseView("generate")} /><NavButton active={view === "jobs"} icon={<ListTodo size={16} />} label="任务中心" onClick={() => chooseView("jobs")} count={activeJobCount} /><NavButton active={view === "runtime"} icon={<Activity size={16} />} label="运行状态" onClick={() => chooseView("runtime")} /></nav><div className="top-actions"><span className={`connection-pill ${config.data?.ready ? "ready" : ""}`}><i />{config.data?.ready ? "API READY" : "需要配置"}</span><button type="button" className="icon-button" title="设置" onClick={() => setSettingsOpen(true)}><Settings2 size={17} /></button></div></header><main className="main-content"><section className="command-bar"><div><span className="command-line"><span className="live-pulse" />LOCAL SESSION / TRACEABLE BY DEFAULT</span><p>你的创作记录，终于有一个能回去找的地方。</p></div><div className="command-stats"><span><b>{statData.generated}</b> 生成</span><span><b>{statData.uploaded}</b> 上传</span><span><b>{statData.synced}</b> 已同步</span></div></section>{view === "gallery" ? <GalleryView assets={assets.data?.items || []} filters={filters} setFilters={setFilters} selected={selected} setSelected={setSelected} onOpen={setSelectedAssetId} onUpload={openUpload} onGenerate={() => chooseView("generate")} setNotice={setNotice} /> : null}{view === "generate" ? <GenerateView config={config.data} providers={providers.data || []} onCreated={() => { setView("jobs"); queryClient.invalidateQueries({ queryKey: ["jobs"] }); queryClient.invalidateQueries({ queryKey: ["stats"] }); }} setNotice={setNotice} /> : null}{view === "jobs" ? <JobsView jobs={jobs.data || []} onOpenAsset={(id) => setSelectedAssetId(id)} setNotice={setNotice} /> : null}{view === "runtime" ? <RuntimeView runtime={runtime.data} setNotice={setNotice} /> : null}</main><footer><span>FRAMELAB / LOCAL ONLY</span><span>ORIGINALS STAY ON THIS MACHINE</span><span className="footer-path">{config.data?.media_dir || config.data?.data_dir || "图片目录读取中"}</span></footer><input ref={fileInput} type="file" accept="image/png,image/jpeg,image/webp,image/gif" hidden onChange={handleFile} />{selectedAssetId ? <AssetDrawer assetId={selectedAssetId} onClose={() => setSelectedAssetId(null)} onChanged={() => { queryClient.invalidateQueries({ queryKey: ["assets"] }); }} setNotice={setNotice} /> : null}{settingsOpen ? <SettingsDrawer config={config.data} providers={providers.data || []} onClose={() => setSettingsOpen(false)} setNotice={setNotice} /> : null}{notice ? <div className="toast"><Info size={16} /><span>{notice}</span><button type="button" onClick={() => setNotice("")} aria-label="关闭提示"><X size={14} /></button></div> : null}</div>;
}
