# HTTP API Protocol

Impact Analysis AI exposes a JSON HTTP API for creating code-impact analyses, polling progress, reading report data, configuring models, editing review standards, and maintaining per-project business context.

Base URL:

```text
http://{host}:{port}
```

Default local URL:

```text
http://127.0.0.1:8080
```

All JSON endpoints use UTF-8 and return `Content-Type: application/json`.

## Status

### `GET /health`

Returns service health.

Response `200`:

```json
{
  "status": "ok"
}
```

## Analysis Jobs

### `POST /api/analyses`

Creates an impact-analysis job. In the configured runtime this endpoint runs asynchronously and normally returns `202`.

Request:

```json
{
  "git_url": "https://github.com/Kong/kong.git",
  "branch": "master",
  "before_commit": "2eb7511",
  "after_commit": "9ee35fd",
  "project_name": "Kong-kong",
  "provider_id": "deepseek",
  "call_graph_depth": 2
}
```

Fields:

| Field | Type | Required | Description |
|---|---:|---:|---|
| `git_url` | string | yes | Git repository URL. The service clones or updates it under `IMPACT_AI_WORKSPACE_ROOT`. |
| `branch` | string | yes | Branch name to fetch and validate. |
| `before_commit` | string | yes | Base commit. Deleted functions are traced from this snapshot. |
| `after_commit` | string | yes | Head commit. Added and modified functions are traced from this snapshot. |
| `project_name` | string | no | Project display/storage name. If omitted, it is inferred from `git_url`, such as `Kong-kong`. |
| `provider_id` | string | yes | AI provider id from `GET /api/providers`. |
| `call_graph_depth` | integer | no | Call-chain depth. Values are clamped to `1..5`; default is `2`. |

Response `202`:

```json
{
  "job": {
    "id": "9b3c0d...",
    "status": "running",
    "request": {
      "git_url": "https://github.com/Kong/kong.git",
      "branch": "master",
      "before_commit": "2eb7511",
      "after_commit": "9ee35fd",
      "project_name": "Kong-kong",
      "provider_id": "deepseek",
      "call_graph_depth": 2
    },
    "created_at": "2026-06-09T05:00:00.000000+00:00",
    "updated_at": "2026-06-09T05:00:01.000000+00:00",
    "result": null,
    "error": null,
    "progress": ["queued", "running"],
    "logs": []
  }
}
```

Error responses:

| Status | Body | Meaning |
|---:|---|---|
| `400` | `{"error":"invalid_request","fields":["git_url"]}` | Required fields are missing or invalid. |
| `400` | `{"error":"unsupported_provider","provider_id":"x","supported_providers":[...]}` | Unknown model provider. |
| `500` | `{"job":{...,"status":"failed","error":"..."}}` | Synchronous analysis failed. |

### `GET /api/analyses`

Lists jobs in newest-first order.

Response `200`:

```json
{
  "jobs": [
    {
      "id": "9b3c0d...",
      "status": "completed",
      "request": {},
      "created_at": "2026-06-09T05:00:00.000000+00:00",
      "updated_at": "2026-06-09T05:03:20.000000+00:00",
      "result": {},
      "error": null,
      "progress": ["queued", "running", "checkout_repository", "completed"],
      "logs": []
    }
  ]
}
```

### `GET /api/analyses/{job_id}`

Returns a single job, including the full analysis report when complete.

Response `200`:

```json
{
  "job": {
    "id": "9b3c0d...",
    "status": "completed",
    "request": {},
    "created_at": "2026-06-09T05:00:00.000000+00:00",
    "updated_at": "2026-06-09T05:03:20.000000+00:00",
    "result": {
      "project_name": "Kong-kong",
      "changed_functions": [],
      "call_graph": {},
      "impact_summary": "中文影响面分析...",
      "review_findings": [],
      "test_cases": [],
      "structured_review_findings": [],
      "structured_test_cases": [],
      "prompt_chunks": 1,
      "token_usage": {}
    },
    "error": null,
    "progress": [],
    "logs": []
  }
}
```

Response `404`:

```json
{
  "error": "not_found"
}
```

## Analysis Report Object

The report is stored at `job.result`.

### `changed_functions[]`

Each item describes a function extracted from the Git diff.

```json
{
  "qualified_name": "kong.pdk.request.get_header",
  "language": "lua",
  "file_path": "kong/pdk/request.lua",
  "signature": "function _REQUEST.get_header(name)",
  "diff_hunk": "@@ ...",
  "change_type": "modified"
}
```

`change_type` is `added`, `modified`, or `deleted`.

### `call_graph`

Two-way call-chain data from `codebase-memory-mcp`, with source-scan fallback data when the graph cannot return a full chain.

```json
{
  "depth": 2,
  "inbound": {
    "kong.pdk.request.get_header": ["callerA", "callerB"]
  },
  "outbound": {
    "kong.pdk.request.get_header": ["calleeA"]
  },
  "trace_status": {
    "kong.pdk.request.get_header": "graph"
  },
  "trace_errors": {
    "kong.pdk.request.get_header": ""
  }
}
```

Common `trace_status` values:

| Value | Meaning |
|---|---|
| `graph` | `codebase-memory-mcp` returned usable graph links. |
| `graph+source` | Graph links were returned and source scanning added supplemental links. |
| `source_fallback` | Graph data was incomplete; source scanning supplied fallback links. |
| `missing_in_graph` | Some changed functions were not found in the graph. |
| `index_failed` | Graph indexing failed or did not produce a usable project id. |
| `unknown` | No status was recorded. |

### `structured_review_findings[]`

AI-generated review findings. String-only clients may use `review_findings[]`; structured clients should prefer this array.

```json
{
  "function": "kong.pdk.request.get_header",
  "business_feature": "网关请求处理",
  "business_subfeature": "请求头读取",
  "impact_level": "medium",
  "standard": "正确性",
  "severity": "medium",
  "finding": "需要验证大小写归一化后的请求头读取行为。",
  "impacted_callers": ["kong.plugins.example.access"]
}
```

`impact_level` and `severity` use `critical`, `high`, `medium`, or `low`.

### `structured_test_cases[]`

AI-generated test cases and test tags. String-only clients may use `test_cases[]`; structured clients should prefer this array.

```json
{
  "test_id": "TC001",
  "name": "请求头读取回归验证",
  "business_feature": "网关请求处理",
  "business_feature_en": "Gateway Request Handling",
  "business_subfeature": "请求头读取",
  "business_subfeature_en": "Header Query",
  "impact_level": "medium",
  "type": "regression",
  "target": "kong.pdk.request.get_header",
  "verification_goal": "verify request header query remains stable",
  "affected_business_flows": ["请求头读取 (Header Query)"],
  "changed_symbols": ["kong.pdk.request.get_header"],
  "affected_files": ["kong/pdk/request.lua"],
  "test_tags": ["header-query", "gateway-request"],
  "covers": ["验证改动函数和上游调用方的请求头读取行为。"]
}
```

`type` is usually `unit`, `integration`, `regression`, `contract`, or `e2e`.

### `token_usage`

Token-budget telemetry for input splitting and AI request sizing.

```json
{
  "max_input_tokens": 64000,
  "max_output_tokens": 8192,
  "reserved_output_tokens": 8192,
  "prompt_chunks": 1,
  "chunk_input_tokens": [12000]
}
```

## Progress And Logs

`job.progress` is a stage list for progress bars. `job.logs` is an ordered list with timestamps and details.

Common stages:

```text
queued
running
checkout_repository
index_repository
extract_changed_functions
changed_functions
trace_call_graph
two_hop_call_graph
prompt_build
ai_request
ai_response
completed
failed
```

Log item:

```json
{
  "time": "2026-06-09T05:00:01.000000+00:00",
  "stage": "index_repository",
  "level": "info",
  "message": "构建 codebase-memory-mcp 知识图谱索引",
  "detail": ""
}
```

## Providers And Model Configuration

### `GET /api/providers`

Returns the supported AI provider catalog.

Response `200`:

```json
{
  "providers": [
    {
      "id": "deepseek",
      "name": "DeepSeek",
      "family": "china",
      "default_model": "deepseek-chat",
      "model_env": "DEEPSEEK_MODEL",
      "api_key_env": "DEEPSEEK_API_KEY",
      "base_url_env": "DEEPSEEK_BASE_URL",
      "default_base_url": "https://api.deepseek.com/v1",
      "max_input_tokens": 64000,
      "max_output_tokens": 8192,
      "api_format": "openai_compatible"
    }
  ]
}
```

### `GET /api/model-configs`

Returns saved model configuration, merged with provider defaults.

Response `200`:

```json
{
  "default_provider_id": "deepseek",
  "configs": [
    {
      "provider_id": "deepseek",
      "provider_name": "DeepSeek",
      "family": "china",
      "is_default": true,
      "model": "deepseek-chat",
      "base_url": "https://api.deepseek.com/v1",
      "api_key": "sk-...",
      "api_key_configured": true,
      "model_env": "DEEPSEEK_MODEL",
      "api_key_env": "DEEPSEEK_API_KEY",
      "base_url_env": "DEEPSEEK_BASE_URL",
      "default_model": "deepseek-chat",
      "default_base_url": "https://api.deepseek.com/v1",
      "max_input_tokens": 64000,
      "max_output_tokens": 8192,
      "api_format": "openai_compatible",
      "supports_response_format": true
    }
  ]
}
```

### `PUT /api/model-configs/{provider_id}`

Saves API key, base URL, or model override.

Request:

```json
{
  "model": "deepseek-chat",
  "base_url": "https://api.deepseek.com/v1",
  "api_key": "sk-..."
}
```

`api_key` may be omitted to preserve the existing key.

### `POST /api/model-configs/default`

Sets the default provider used by the dashboard form.

Request:

```json
{
  "provider_id": "deepseek"
}
```

Response:

```json
{
  "default_provider_id": "deepseek"
}
```

### `POST /api/model-configs/{provider_id}/test`

Runs a short model reachability probe.

Success `200`:

```json
{
  "ok": true,
  "provider_id": "deepseek",
  "model": "deepseek-chat",
  "response": {
    "ok": true,
    "message": "model is reachable"
  }
}
```

Failure `502`:

```json
{
  "ok": false,
  "provider_id": "deepseek",
  "model": "deepseek-chat",
  "error": "..."
}
```

## Review Standards

### `GET /api/review-standards`

Returns all language review standards.

### `GET /api/review-standards/{language}`

Returns one language standard, or the generic fallback for unknown languages.

### `PUT /api/review-standards/{language}`

Replaces a language standard.

Request:

```json
{
  "sections": {
    "正确性": ["检查空值、边界条件和异常路径。"],
    "安全性": ["检查认证、授权和敏感数据输出。"]
  }
}
```

## Project Business Context

### `GET /api/projects/{project_name}/business-context`

Returns project-specific Markdown context.

Response:

```json
{
  "project_name": "Kong-kong",
  "business_context": "业务说明 Markdown",
  "source_path": "profiles/Kong-kong/business.md"
}
```

### `PUT /api/projects/{project_name}/business-context`

Saves project-specific Markdown context.

Request:

```json
{
  "business_context": "这里填写业务说明、接口约束、核心风险规则。"
}
```

## Dashboard

### `GET /` and `GET /dashboard`

Return the Chinese dashboard HTML.

## Polling Pattern

Recommended client flow:

1. Configure model credentials with `PUT /api/model-configs/{provider_id}`.
2. Optionally test the model with `POST /api/model-configs/{provider_id}/test`.
3. Save project business context with `PUT /api/projects/{project_name}/business-context`.
4. Create an analysis with `POST /api/analyses`.
5. Poll `GET /api/analyses/{job_id}` every 2-5 seconds until `status` is `completed` or `failed`.
6. Read `job.result` as the HTTP analysis report.

## Curl Example

```bash
curl -sS http://127.0.0.1:8080/api/analyses \
  -H 'Content-Type: application/json' \
  -d '{
    "git_url": "https://github.com/Kong/kong.git",
    "branch": "master",
    "before_commit": "2eb7511",
    "after_commit": "9ee35fd",
    "project_name": "Kong-kong",
    "provider_id": "deepseek",
    "call_graph_depth": 2
  }'
```
