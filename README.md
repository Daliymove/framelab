# FrameLab

FrameLab 是一个仅在本机运行的个人 AI 图库。它把上传图片和生成图片统一保存到本地，并记录原始 prompt、provider、模型、请求参数、任务状态、耗时、父任务和图床同步信息。

## 当前能力

- React 图库、生成工作台和任务中心
- PNG、JPEG、WebP、GIF 原图无损保存
- SQLite 持久任务队列，关闭浏览器后任务继续运行
- OpenAI 兼容异步生图 provider，固定走 `/images/generations/async`
- 参考图改图、局部修改和风格迁移，按 SHUAI API 约定走 `/images/edits/async` multipart，参考图字段为 `image[]`
- 不重复提交生图；查询连接中断时继续等待原 provider 任务
- 手动重试会创建子任务记录，可恢复仍有 task ID 的原任务
- CloudFlare-ImgBed REST 上传，固定设置 `autoRetry=false`
- 每张图片可选择自动同步或仅本地保存
- 标题、备注、可编辑 prompt、标签和批量同步
- SHA-256、尺寸、文件大小、完整生成参数和事件时间线

## 启动

先复制配置模板并填写项目根目录下的 `.env`：

```powershell
Copy-Item .env.example .env
```

```dotenv
CODEX_IMAGE_API_KEY=你的Key
CODEX_IMAGE_BASE_URL=https://example.com/v1
```

`.env` 会在 API 和 worker 启动时自动加载，修改后需要重启两个进程。可选的本地 CloudFlare-ImgBed 配置也写在同一文件：

```dotenv
FRAMELAB_IMGBED_BASE_URL=http://127.0.0.1:8080
FRAMELAB_IMGBED_API_TOKEN=imgbed_...
```

图片存储目录也可以在网页右上角的“运行设置”中修改。保存时会迁移已有原图和缩略图，数据库仍保留在数据目录；也可以在 `.env` 中设置 `FRAMELAB_MEDIA_DIR`。

在“开始创作”中上传参考图后，输入修改或美化 prompt 即可提交图生图任务。参考图会先保存到本地图库，任务只记录资产 ID；worker 提交时调用 `POST /v1/images/edits/async`，以 multipart 的 `image[]` 发送原图，再用 `GET /v1/images/tasks/{task_id}` 轮询。SHUAI API 的异步改图请求体上限为 15MB，超过时请先压缩参考图；不选择参考图时仍使用 `/v1/images/generations/async`。

然后双击 `start.cmd`，或运行：

```powershell
.\start.ps1
```

默认地址：<http://127.0.0.1:8765>

首次启动会在项目内创建 `.venv`、安装依赖并构建前端。运行数据默认保存到 `%USERPROFILE%\Pictures\FrameLab`，不在 Git 仓库中。可在 `.env` 中设置 `FRAMELAB_DATA_DIR`，或使用 `-DataDir` 修改；图片文件可单独通过 `FRAMELAB_MEDIA_DIR` 配置。

## 目录

```text
framelab/        FastAPI、SQLite、worker 和外部适配器
web/src/         React + TypeScript 界面
tests/           不调用真实外部服务的测试
server.py        本地 API 入口
start.ps1        一键启动 API、worker 和浏览器
```

## 开发检查

```powershell
python -m pytest -q
npm run build --prefix web
```

测试使用本地模拟 provider，不会真实生成图片或上传图床。
