## 项目说明

这是一个基于 FastAPI 的验证码识别服务，后端调用 Gemini 进行图像识别，并提供两种用法：

- **同步**：接口会等待 Gemini 返回后再响应
- **轮询**：第一次请求只触发后台任务；后续按 `task_id` 查询状态/结果（适合“每秒调用一次、但不想阻塞等待”的场景）

所有接口默认启用 Header 密钥认证（`X-API-SECRET`）。

## Docker 快捷部署（推荐）

1) 在项目根目录准备 `.env`（不要提交到 git），可从 `.env.example` 复制：

```
GEMINI_API_KEY=xxx
GEMINI_MODEL=gemini-2.5-flash
API_SECRET=your-secret
```

2) 一键启动：

```
docker compose up -d --build
```

服务默认监听 `http://localhost:8000`，OpenAPI 文档：`http://localhost:8000/docs`

说明：

- Docker 镜像内依赖安装使用 `uv` + `uv.lock`（与本地 `uv sync` 一致），避免“本地能跑、线上不一致”的问题

常用命令：

- 查看日志：`docker compose logs -f`
- 停止：`docker compose down`

## 本地运行（非 Docker）

```
uv sync
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

确保进程环境里有这些变量（或放在项目根目录 `.env` 中）：

- `GEMINI_API_KEY`：必填
- `GEMINI_MODEL`：可选，默认 `gemini-2.5-flash`
- `API_SECRET`：必填（用于请求头认证）

## 认证方式

所有接口都需要请求头：

- `X-API-SECRET: your-secret`

## API

### 同步（等待 Gemini 返回）

- `POST /ocr`：body `{"image":"<base64>"}`
- `POST /ocr/upload`：form-data 上传文件字段 `file`

### 轮询（后台任务）

- `POST /ocr/poll`：body `{"image":"<base64>"}`，首次触发任务；如果已完成会直接返回结果
- `POST /ocr/upload/poll`：上传文件触发任务
- `GET /ocr/task/{task_id}`：按 `task_id` 查询状态/结果

返回格式（轮询接口统一）：

- `status=pending`：任务还在跑
- `status=done`：返回 `code/raw`
- `status=error`：返回 `error`

`retry=true`：对同一张图片如果之前失败，强制重新触发一次后台任务。

## 调用示例

### PowerShell（Windows）

```powershell
$b64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes("captcha.png"))
$body = @{ image = $b64 } | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost:8000/ocr/poll" `
  -Headers @{ "X-API-SECRET" = "your-secret" } `
  -ContentType "application/json" `
  -Body $body
```

如果返回 `status=pending`，可按 `task_id` 轮询：

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "http://localhost:8000/ocr/task/<task_id>" `
  -Headers @{ "X-API-SECRET" = "your-secret" }
```

### curl

```bash
curl -sS -X POST "http://localhost:8000/ocr/poll" \
  -H "Content-Type: application/json" \
  -H "X-API-SECRET: your-secret" \
  -d '{"image":"<base64>"}'
```

## 运行参数与资源占用

- 后台任务调用 Gemini 默认超时 `30s`（超时会变为 `status=error`）
- 结果/错误缓存默认保留 `10min`，且最多保存 `200` 条（超过会淘汰最旧的），避免内存无限增长
