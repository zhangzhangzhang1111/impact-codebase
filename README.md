# Impact Analysis AI

HTTP service for code-change impact analysis. The service is designed to accept a git URL, branch, before/after commits, and project name, then combine changed functions with a two-hop code knowledge graph, AI analysis, review standards, and generated test cases.

Current slice:

- AI provider catalog for mainstream global and China-market providers.
- Token budget helper for prompt chunking and output reservation.
- Per-project `business.md` loader under a profile root.
- Default review standards for common languages.
- Analysis pipeline core with pluggable knowledge-graph and AI clients.
- Git diff function extraction for added, modified, and deleted functions across Python, JavaScript, TypeScript, Java, Go, Rust, PHP, C#, Kotlin, C++, Ruby, and Swift commit ranges.
- Each changed function includes `change_type` (`added`, `modified`, or `deleted`) so impact analysis, review findings, and test generation can distinguish creation, behavior changes, and removals.
- codebase-memory-mcp knowledge graph adapter contract for clone, index, and two-hop call tracing.
- Git checkout validation ensures the requested branch exists, both commits exist, `before_commit` reaches `after_commit`, and repository indexing runs against the exact `after_commit` snapshot rather than a later branch tip.
- Deleted functions are traced against a separate `before_commit` codebase-memory index, while added and modified functions are traced against the `after_commit` index.
- Reused project workspaces synchronize `remote.origin.url` with the submitted git URL before fetching, so repeated analyses with the same project name do not silently use an old repository.
- AI client support for OpenAI-compatible chat completions, Anthropic Messages, and Gemini GenerateContent APIs.
- HTTP dashboard shell with provider catalog, queued/completed analysis history, and persisted analysis results.
- Project-level `business.md` management through HTTP APIs and the dashboard.
- Review standards catalog through HTTP APIs and the dashboard.
- Default runtime assembly from environment variables. Running `python3 -m impact_ai.http_server` wires the codebase-memory CLI client, git checkout workspace, profile loader, token budget, and AI client.
- Async HTTP analysis execution with persisted progress stages. The dashboard shows the full stage chain, including repository checkout, indexing, diff extraction, call graph tracing, prompt building, AI request/response, and final completion or failure.
- Dashboard analysis detail expands changed functions, inbound callers, outbound callees, review findings, structured review findings, generated tests, structured test cases, and token usage for completed jobs.
- Token budget telemetry in every analysis result, including prompt chunk count, estimated input tokens per chunk, reserved output tokens, and max output tokens sent to the AI provider.

Important extension points:

- `impact_ai.codebase_memory_graph.CodebaseMemoryKnowledgeGraph` combines local git checkout, changed-function extraction, repository indexing, and two-hop graph tracing through a `CodebaseMemoryClient`.
- `impact_ai.codebase_memory_cli.CodebaseMemoryCliClient` invokes `codebase-memory-mcp cli index_repository` with `repo_path`, `project_name`, and index mode, then calls `codebase-memory-mcp cli trace_path`; trace normalization removes the changed function itself from caller/callee lists.
- `impact_ai.ai_client.OpenAICompatibleClient` calls providers using the common `/chat/completions` API shape and expects JSON object content.
- `impact_ai.analysis.ImpactAnalyzer` is the core orchestration layer used by HTTP when an analyzer is supplied to `create_server(..., analyzer=...)`.
- `impact_ai.runtime.create_configured_server` starts the default server in async mode so the dashboard can poll job progress and history.

Progress stages:

- Job lifecycle: `queued`, `running`, `completed`, `failed`.
- Analyzer stages: `changed_functions`, `two_hop_call_graph`, `prompt_build`, `ai_request`, `ai_response`.
- codebase-memory graph stages: `checkout_repository`, `index_repository`, `extract_changed_functions`, `trace_call_graph`.

Token limits:

- The analyzer uses each provider's configured `max_input_tokens` and `max_output_tokens` unless explicit `IMPACT_AI_*` token limits are provided; explicit limits are still capped at the selected provider's catalog limits.
- Input prompts are split into chunks after reserving output space, so each AI request stays within the available input budget.
- Oversized context tokens, such as minified code or very long diff lines without whitespace, are split at character boundaries to keep each prompt chunk within budget.
- Every prompt chunk is a self-contained JSON envelope with task, project metadata, output contract, and chunk metadata, rather than a raw slice of partial JSON.
- AI calls receive `max_tokens` equal to the configured max output budget.
- AI response parsing accepts strict JSON objects and common JSON wrapped in Markdown code fences or surrounding explanatory text.
- Results include `token_usage` with `prompt_chunks`, `chunk_input_tokens`, `reserved_output_tokens`, `max_input_tokens`, and `max_output_tokens`; the dashboard displays the chunk count and max output budget in analysis history.

Analysis result shape:

- `review_findings` and `test_cases` remain string lists for simple clients and backward compatibility.
- `structured_review_findings` preserves AI-provided review objects with fields such as function, standard, severity, finding, and impacted callers.
- `structured_test_cases` preserves AI-provided test case objects with fields such as name, type, target, and covered behavior.

Business context:

- `GET /api/projects/{project_name}/business-context` returns the project's configured `business.md` content.
- `PUT /api/projects/{project_name}/business-context` writes `{"business_context": "..."}` to `{IMPACT_AI_PROFILE_ROOT}/{safe_project_name}/business.md`; project names are normalized to a safe directory slug so slashes, spaces, and traversal-like segments cannot escape the profile root.
- The dashboard includes a Business Context editor so project-specific domain rules can be maintained before analysis runs.

Review standards:

- `GET /api/review-standards` returns all default language review standards.
- `GET /api/review-standards/{language}` returns a single language standard, or the generic fallback for unknown languages.
- The dashboard includes a Review Standards table so reviewers can inspect the default checks before running analysis.

Environment:

- `CODEBASE_MEMORY_MCP_BIN`: codebase-memory binary path, defaults to `codebase-memory-mcp`.
- `CODEBASE_MEMORY_INDEX_MODE`: codebase-memory index mode, defaults to `fast`; set `full` for full indexing when needed.
- `IMPACT_AI_MANAGE_CODEBASE_MEMORY`: manage the `codebase-memory-mcp` process with the HTTP service, defaults to `true`; set `false` when another supervisor owns it.
- `CODEBASE_MEMORY_ENABLE_UI`: enable the codebase-memory graph UI before launching the managed process, defaults to `true`.
- `CODEBASE_MEMORY_UI_PORT`: codebase-memory graph UI port, defaults to `9749`.
- `IMPACT_AI_HOST`: HTTP bind host, defaults to `127.0.0.1`; use `0.0.0.0` for LAN or container deployment.
- `IMPACT_AI_PORT`: HTTP bind port, defaults to `8080`.
- `IMPACT_AI_WORKSPACE_ROOT`: cloned repository workspace, defaults to `.impact-ai/repos`.
- `IMPACT_AI_HISTORY_PATH`: JSON analysis history path, defaults to `.impact-ai/history.json`.
- `IMPACT_AI_PROFILE_ROOT`: project profile root, defaults to `profiles`.
- `IMPACT_AI_MAX_INPUT_TOKENS`, `IMPACT_AI_MAX_OUTPUT_TOKENS`, `IMPACT_AI_RESERVED_OUTPUT_TOKENS`: optional fixed token budget override.
- Provider credentials, base URLs, and models are configurable with each provider's `*_API_KEY`, `*_BASE_URL`, and `*_MODEL` variables. Examples: `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `OPENAI_MODEL`, `DEEPSEEK_API_KEY`, `DEEPSEEK_BASE_URL`, `DEEPSEEK_MODEL`, `QWEN_MODEL`, `ANTHROPIC_MODEL`, and `GEMINI_MODEL`.
- `GET /api/providers` and the dashboard Providers table expose each provider's API key, base URL, and model environment variable names.

AI provider API styles:

- OpenAI-compatible `/chat/completions`: OpenAI, DeepSeek, Qwen, Zhipu GLM, Moonshot Kimi, Doubao, Tencent Hunyuan, Baidu ERNIE.
- Anthropic native Messages API: Anthropic Claude.
- Gemini native `models/{model}:generateContent` API: Google Gemini.

Run:

```bash
./scripts/run.sh
```

Then open `http://127.0.0.1:8080`.

For LAN or container deployment:

```bash
IMPACT_AI_HOST=0.0.0.0 IMPACT_AI_PORT=8080 ./scripts/run.sh
```

When process management is enabled, starting the HTTP service also starts a managed `codebase-memory-mcp` child process and configures the graph UI at `http://localhost:9749`. Stopping the HTTP service closes the child process.

Runtime dependencies:

- Python 3.12 or newer.
- Git.
- `codebase-memory-mcp`.

The project itself uses only the Python standard library. `scripts/run.sh` first looks for a bundled platform archive at `vendor/codebase-memory-mcp/{platform}/codebase-memory-mcp.tar.gz`, extracts it into `.impact-ai/bin/`, then falls back to `CODEBASE_MEMORY_MCP_BIN`, then to `codebase-memory-mcp` on `PATH`.

Bundled `codebase-memory-mcp` v0.7.0 archives:

- `vendor/codebase-memory-mcp/darwin-arm64/codebase-memory-mcp.tar.gz`
- `vendor/codebase-memory-mcp/darwin-amd64/codebase-memory-mcp.tar.gz`
- `vendor/codebase-memory-mcp/linux-amd64/codebase-memory-mcp.tar.gz`
- `vendor/codebase-memory-mcp/linux-arm64/codebase-memory-mcp.tar.gz`

Install or refresh `codebase-memory-mcp` for the current Linux/macOS platform:

```bash
./scripts/install_codebase_memory.sh
```

By default this downloads the latest official release binary into `vendor/codebase-memory-mcp/{platform}/`. To pin a version:

```bash
CODEBASE_MEMORY_VERSION=v0.7.0 ./scripts/install_codebase_memory.sh
```

Build a downloadable package for the current platform:

```bash
./scripts/prepare_release.sh
```

The archive is written to `dist/impact-codebase-{version}-{platform}.tar.gz`. It includes source code, tests, scripts, `.codebase-memory` seed artifact, and the vendored `codebase-memory-mcp` archives for Linux/macOS. After extracting the archive on Linux/macOS:

```bash
./scripts/run.sh
```

The HTTP API protocol is documented in `docs/http-api.md`.

Test:

```bash
python3 -m unittest discover -s tests
```

Optional real codebase-memory CLI smoke:

```bash
RUN_CODEBASE_MEMORY_CLI_INTEGRATION=1 python3 -m unittest tests/test_codebase_memory_cli_integration.py
```

The smoke uses the real `codebase-memory-mcp` binary and may need permission to write the tool's default persistent graph storage outside the project workspace.
