# Impact Codebase

Impact Codebase 是一个面向 AI 编程助手的代码改动影响面分析服务。它通过 HTTP 接收 Git 仓库地址、分支、修改前后 commit、项目名称和模型配置，自动提取 Git Diff 中的改动函数，结合 `codebase-memory-mcp` 代码知识图谱追踪调用链路，再调用 AI 生成中文影响面分析、代码评审发现和测试用例建议。

## 核心能力

- 支持通过看板创建、查看和追踪代码影响面分析任务。
- 支持 Git 仓库自动克隆/更新，并校验分支、commit 和祖先关系。
- 支持提取新增、修改、删除函数，覆盖 Python、JavaScript、TypeScript、Java、Go、Rust、PHP、C#、Kotlin、C++、Ruby、Swift、Lua 等常见语言。
- 集成 `DeusData/codebase-memory-mcp`，对改动函数追踪入向调用方和出向被调方。
- 支持分析时配置调用链路层级，当前限制为 `1..5`，默认 `2`。
- 支持 `codebase-memory-mcp` 不完整时使用源码扫描补齐调用关系。
- 支持项目级 `business.md` 业务说明，作为 AI 分析上下文。
- 支持默认代码评审规范，并可在页面按语言修改。
- 支持模型配置、默认模型选择和模型可用性测试。
- 支持分析进度条、阶段日志、失败原因和历史结果持久化。
- 输出中文影响面分析、结构化评审发现、结构化测试用例、测试标签和 token 使用情况。

## AI 模型支持

当前内置国内外主流模型提供商：

- OpenAI
- Anthropic Claude
- Google Gemini
- DeepSeek
- Alibaba Qwen
- Zhipu GLM
- Moonshot Kimi
- ByteDance Doubao
- Tencent Hunyuan
- Baidu ERNIE

模型配置可通过页面维护，也可以通过环境变量覆盖。页面支持保存 API Key、Base URL、模型名，设置默认模型，并点击测试按钮验证模型是否可用。

## 内网部署说明

仓库已经内置 `codebase-memory-mcp v0.7.0` 的 Linux/macOS 运行时归档，内网机器不需要再联网下载图谱引擎。

内置归档位置：

```text
vendor/codebase-memory-mcp/darwin-arm64/codebase-memory-mcp.tar.gz
vendor/codebase-memory-mcp/darwin-amd64/codebase-memory-mcp.tar.gz
vendor/codebase-memory-mcp/linux-amd64/codebase-memory-mcp.tar.gz
vendor/codebase-memory-mcp/linux-arm64/codebase-memory-mcp.tar.gz
```

`./scripts/run.sh` 会根据当前系统自动选择对应归档，解压到 `.impact-ai/bin/` 后启动服务。

运行依赖：

- Python 3.6 或更高版本
- Git
- tar

Python 3.6 需要 `dataclasses` backport；仓库已将对应 wheel 放在 `vendor/python/`，`requirements.txt` 默认只从本地 wheel 安装依赖，`./scripts/run.sh` 也会自动把这些 wheel 加入 `PYTHONPATH`。

仓库内置脱敏模型配置模板：

```text
config/model_config.default.json
```

首次启动且 `.impact-ai/model_config.json` 不存在时，服务会自动复制该模板作为本地运行配置。模板不包含任何 API Key，默认分析模型为 `deepseek`；部署后只需要在页面或本地 `.impact-ai/model_config.json` 中填入对应 provider 的 API Key 即可使用。`.impact-ai/` 目录仍然被 Git 忽略，用来保存本机真实密钥和运行历史。

## 快速启动

本机启动：

```bash
./scripts/run.sh
```

打开：

```text
http://127.0.0.1:8080
```

内网或容器部署时监听所有网卡：

```bash
IMPACT_AI_HOST=0.0.0.0 IMPACT_AI_PORT=8080 ./scripts/run.sh
```

启动 HTTP 服务时默认会一起启动托管的 `codebase-memory-mcp` MCP/UI 子进程；这些子进程以非阻塞方式启动，关闭 HTTP 服务时也会停止。

## MCP 接入

服务同时提供 MCP 适配，方便 Cursor、Claude Desktop、Codex 等支持 MCP 的 AI 工具调用影响面分析能力。

HTTP 传输：

```text
POST http://127.0.0.1:8080/mcp
```

该入口支持 JSON-RPC 2.0 的 `initialize`、`ping`、`tools/list`、`tools/call` 方法。可用工具包括：

- `analyze_code_impact`：同步执行影响面分析，参数与 HTTP `POST /api/analyses` 基本一致。
- `list_analysis_jobs`：查看历史分析任务。
- `get_analysis_job`：按任务 ID 查看历史任务详情。
- `list_ai_providers`：查看支持的 AI provider。

stdio 传输：

```bash
./scripts/run_mcp.sh
```

AI 工具的 MCP 配置可将该脚本作为 command。stdio 模式使用 MCP 常见的 `Content-Length` JSON-RPC 帧；启动诊断信息输出到 stderr，不会污染协议 stdout。

## Release 产物

生成可直接分发的 release 包：

```bash
./scripts/prepare_release.sh
```

产物输出到：

```text
dist/impact-codebase-{commit}-{platform}.tar.gz
dist/impact-codebase-{commit}-{platform}.tar.gz.sha256
```

release 包包含：

- 源码和测试
- 中文 README
- HTTP API 协议文档
- 启动和依赖脚本
- `.codebase-memory` 初始图谱产物
- Linux/macOS 四个平台的 `codebase-memory-mcp` 运行时归档
- Python 3.6 所需的离线 wheel 依赖
- 脱敏默认模型配置模板
- HTTP `/mcp` 与 stdio MCP 启动脚本

解压后运行：

```bash
./scripts/run.sh
```

校验包完整性：

```bash
sha256sum -c impact-codebase-{commit}-{platform}.tar.gz.sha256
```

macOS 可使用：

```bash
shasum -a 256 -c impact-codebase-{commit}-{platform}.tar.gz.sha256
```

## 常用环境变量

| 变量 | 说明 | 默认值 |
|---|---|---|
| `IMPACT_AI_HOST` | HTTP 监听地址 | `127.0.0.1` |
| `IMPACT_AI_PORT` | HTTP 监听端口 | `8080` |
| `IMPACT_AI_WORKSPACE_ROOT` | 被分析仓库克隆目录 | `.impact-ai/repos` |
| `IMPACT_AI_HISTORY_PATH` | 分析历史 JSON 文件 | `.impact-ai/history.json` |
| `IMPACT_AI_MODEL_CONFIG_PATH` | 模型配置 JSON 文件 | `.impact-ai/model_config.json` |
| `IMPACT_AI_REVIEW_STANDARDS_PATH` | 评审规范 JSON 文件 | `.impact-ai/review_standards.json` |
| `IMPACT_AI_PROFILE_ROOT` | 项目业务说明目录 | `profiles` |
| `IMPACT_AI_MANAGE_CODEBASE_MEMORY` | 是否随服务管理 `codebase-memory-mcp` 进程 | `true` |
| `CODEBASE_MEMORY_MCP_BIN` | 指定 `codebase-memory-mcp` 可执行文件 | 自动从 `vendor/` 解压 |
| `CODEBASE_MEMORY_INDEX_MODE` | 图谱索引模式 | `fast` |
| `CODEBASE_MEMORY_CACHE_DIR` | 图谱缓存目录 | `.impact-ai/codebase-memory-cache` |
| `CODEBASE_MEMORY_ENABLE_UI` | 是否启用图谱 UI 配置 | `true` |
| `CODEBASE_MEMORY_UI_PORT` | 图谱 UI 端口 | `9749` |

Token 预算覆盖：

```text
IMPACT_AI_MAX_INPUT_TOKENS
IMPACT_AI_MAX_OUTPUT_TOKENS
IMPACT_AI_RESERVED_OUTPUT_TOKENS
```

模型环境变量示例：

```text
OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL
DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL
QWEN_API_KEY / QWEN_BASE_URL / QWEN_MODEL
ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / ANTHROPIC_MODEL
ANTHROPIC_COMPATIBLE_API_KEY / ANTHROPIC_COMPATIBLE_BASE_URL / ANTHROPIC_COMPATIBLE_MODEL
GEMINI_API_KEY / GEMINI_BASE_URL / GEMINI_MODEL
```

`anthropic-compatible` 用于第三方 Anthropic Messages 兼容服务，会按 `{base_url}/v1/messages` 请求；OpenAI-compatible 服务继续使用 `openai` 或其他兼容 provider。

## HTTP API 文档

完整接口协议见：

```text
docs/http-api.md
```

核心接口：

- `GET /health`：健康检查。
- `POST /api/analyses`：创建分析任务。
- `GET /api/analyses`：查询分析历史。
- `GET /api/analyses/{job_id}`：查询分析详情和报告结果。
- `GET /api/providers`：查询模型提供商列表。
- `GET /api/model-configs`：查询模型配置。
- `PUT /api/model-configs/{provider_id}`：保存模型配置。
- `POST /api/model-configs/default`：设置默认模型。
- `POST /api/model-configs/{provider_id}/test`：测试模型配置是否可用。
- `GET /api/review-standards`：查询评审规范。
- `PUT /api/review-standards/{language}`：保存语言评审规范。
- `GET /api/projects/{project_name}/business-context`：查询项目业务说明。
- `PUT /api/projects/{project_name}/business-context`：保存项目业务说明。

创建分析示例：

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

## 分析流程

1. 拉取或更新 Git 仓库。
2. 校验分支、修改前 commit、修改后 commit 和提交关系。
3. 在对应 commit 快照上构建 `codebase-memory-mcp` 知识图谱。
4. 从 Git Diff 中提取改动函数。
5. 根据配置的调用链路层级追踪入向/出向调用关系。
6. 合并项目业务说明和语言评审规范。
7. 按模型 token 上限切分提示词。
8. 调用 AI 模型生成影响面分析、评审发现和测试用例。
9. 在看板展示进度、日志、失败原因和历史报告。

## 测试

运行全部单元测试：

```bash
python3 -m unittest discover -s tests
```

运行真实 `codebase-memory-mcp` CLI 冒烟测试：

```bash
RUN_CODEBASE_MEMORY_CLI_INTEGRATION=1 python3 -m unittest tests/test_codebase_memory_cli_integration.py
```

## 当前版本

当前 release tag：

```text
v0.1.0
```

最新内网可部署代码已经包含在 `main` 分支和 `v0.1.0` tag 中。
