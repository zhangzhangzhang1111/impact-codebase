import json
import inspect
import signal
import threading
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol
from urllib.parse import unquote

from impact_ai.analysis import ImpactAnalysisResult
from impact_ai.ai_providers import provider_catalog
from impact_ai.job_store import InMemoryJobStore
from impact_ai.model_config import InMemoryModelConfigStore, ModelConfig
from impact_ai.models import ImpactAnalysisRequest
from impact_ai.project_profiles import ProjectProfile, ProjectProfileLoader
from impact_ai.review_standards import InMemoryReviewStandardStore

REQUIRED_ANALYSIS_FIELDS = (
    "git_url",
    "branch",
    "before_commit",
    "after_commit",
    "provider_id",
)
MODEL_TEST_TIMEOUT_SECONDS = 10


class Analyzer(Protocol):
    def analyze(self, request: ImpactAnalysisRequest) -> ImpactAnalysisResult:
        raise NotImplementedError


def create_server(
    address: tuple[str, int],
    analyzer: Analyzer | None = None,
    execute_async: bool = False,
    job_store: InMemoryJobStore | None = None,
    profile_loader: ProjectProfileLoader | None = None,
    model_config_store: InMemoryModelConfigStore | None = None,
    review_standard_store: InMemoryReviewStandardStore | None = None,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(address, ImpactRequestHandler)
    server.job_store = job_store or InMemoryJobStore()
    server.analyzer = analyzer
    server.execute_async = execute_async
    server.profile_loader = profile_loader
    server.model_config_store = model_config_store or InMemoryModelConfigStore()
    server.review_standard_store = review_standard_store or InMemoryReviewStandardStore()
    return server


class ImpactRequestHandler(BaseHTTPRequestHandler):
    server_version = "ImpactAI/0.1"

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json({"status": "ok"})
            return

        if self.path == "/api/analyses":
            self._send_json({"jobs": [job.to_dict() for job in self.server.job_store.list()]})
            return

        if self.path.startswith("/api/analyses/"):
            job_id = unquote(self.path.removeprefix("/api/analyses/")).strip("/")
            job = self.server.job_store.get(job_id)
            if job is None:
                self._send_json({"error": "not_found"}, status=404)
                return
            self._send_json({"job": job.to_dict()})
            return

        if self.path == "/api/providers":
            self._send_json({"providers": [asdict(provider) for provider in provider_catalog()]})
            return

        if self.path == "/api/model-configs":
            self._send_json(_model_config_catalog(self.server.model_config_store))
            return

        if self.path == "/api/review-standards":
            self._send_json({"standards": [asdict(standard) for standard in self.server.review_standard_store.list()]})
            return

        if self.path.startswith("/api/review-standards/"):
            language = unquote(self.path.removeprefix("/api/review-standards/")).strip("/")
            self._send_json(asdict(self.server.review_standard_store.get(language)))
            return

        profile_project = _business_context_project_from_path(self.path)
        if profile_project:
            if self.server.profile_loader is None:
                self._send_json({"error": "profile_loader_not_configured"}, status=503)
                return
            self._send_json(_profile_to_dict(self.server.profile_loader.load(profile_project)))
            return

        if self.path in {"/", "/dashboard"}:
            self._send_html(_dashboard_html())
            return

        self._send_json({"error": "not_found"}, status=404)

    def do_PUT(self) -> None:
        if self.path.startswith("/api/model-configs/"):
            provider_id = unquote(self.path.removeprefix("/api/model-configs/")).strip("/")
            provider = _provider_by_id(provider_id)
            if provider is None:
                self._send_json({"error": "unsupported_provider", "provider_id": provider_id}, status=404)
                return
            try:
                payload = self._read_json()
            except json.JSONDecodeError:
                self._send_json({"error": "invalid_request"}, status=400)
                return
            api_key = payload.get("api_key") if "api_key" in payload else None
            if api_key is not None and not isinstance(api_key, str):
                self._send_json({"error": "invalid_request"}, status=400)
                return
            model = payload.get("model", "")
            base_url = payload.get("base_url", "")
            if not isinstance(model, str) or not isinstance(base_url, str):
                self._send_json({"error": "invalid_request"}, status=400)
                return
            config = self.server.model_config_store.save(
                provider_id=provider.id,
                model=model,
                base_url=base_url,
                api_key=api_key,
            )
            _apply_model_config_to_analyzer(self.server.analyzer, config)
            self._send_json(_model_config_to_public_dict(config, provider))
            return

        if self.path.startswith("/api/review-standards/"):
            language = unquote(self.path.removeprefix("/api/review-standards/")).strip("/")
            try:
                payload = self._read_json()
                sections = payload["sections"]
                if not isinstance(sections, dict):
                    raise TypeError
                normalized_sections = {}
                for section, items in sections.items():
                    if not isinstance(section, str) or not isinstance(items, list) or not all(isinstance(item, str) for item in items):
                        raise TypeError
                    normalized_sections[section.strip()] = [item.strip() for item in items if item.strip()]
            except (json.JSONDecodeError, KeyError, TypeError):
                self._send_json({"error": "invalid_request"}, status=400)
                return
            standard = self.server.review_standard_store.save(language, normalized_sections)
            _apply_review_standard_to_analyzer(self.server.analyzer, standard)
            self._send_json(asdict(standard))
            return

        profile_project = _business_context_project_from_path(self.path)
        if not profile_project:
            self._send_json({"error": "not_found"}, status=404)
            return
        if self.server.profile_loader is None:
            self._send_json({"error": "profile_loader_not_configured"}, status=503)
            return

        try:
            payload = self._read_json()
            business_context = payload["business_context"]
            if not isinstance(business_context, str):
                raise TypeError
        except (json.JSONDecodeError, KeyError, TypeError):
            self._send_json({"error": "invalid_request"}, status=400)
            return

        profile = self.server.profile_loader.save(profile_project, business_context)
        self._send_json(_profile_to_dict(profile))

    def do_POST(self) -> None:
        if self.path == "/api/model-configs/default":
            try:
                payload = self._read_json()
                provider_id = payload["provider_id"].strip()
            except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
                self._send_json({"error": "invalid_request"}, status=400)
                return
            provider = _provider_by_id(provider_id)
            if provider is None:
                self._send_json({"error": "unsupported_provider", "provider_id": provider_id}, status=404)
                return
            default_provider_id = self.server.model_config_store.set_default_provider_id(provider.id)
            self._send_json({"default_provider_id": default_provider_id})
            return

        if self.path.startswith("/api/model-configs/") and self.path.endswith("/test"):
            provider_id = unquote(self.path.removeprefix("/api/model-configs/").removesuffix("/test")).strip("/")
            provider = _provider_by_id(provider_id)
            if provider is None:
                self._send_json({"error": "unsupported_provider", "provider_id": provider_id}, status=404)
                return
            if self.server.analyzer is None or not hasattr(self.server.analyzer, "ai_client"):
                self._send_json({"ok": False, "error": "ai_client_not_configured"}, status=503)
                return
            try:
                response = _test_model_with_timeout(self.server.analyzer.ai_client, provider)
            except Exception as error:
                self._send_json(
                    {
                        "ok": False,
                        "provider_id": provider.id,
                        "model": _model_name_for_analyzer(self.server.analyzer, provider),
                        "error": str(error),
                    },
                    status=502,
                )
                return
            self._send_json(
                {
                    "ok": True,
                    "provider_id": provider.id,
                    "model": _model_name_for_analyzer(self.server.analyzer, provider),
                    "response": response,
                }
            )
            return

        if self.path != "/api/analyses":
            self._send_json({"error": "not_found"}, status=404)
            return

        try:
            payload = self._read_json()
            invalid_fields = _invalid_analysis_fields(payload)
            if invalid_fields:
                self._send_json({"error": "invalid_request", "fields": invalid_fields}, status=400)
                return
            supported_providers = _supported_provider_ids()
            if payload["provider_id"].strip() not in supported_providers:
                self._send_json(
                    {
                        "error": "unsupported_provider",
                        "provider_id": payload["provider_id"],
                        "supported_providers": supported_providers,
                    },
                    status=400,
                )
                return
            request = ImpactAnalysisRequest(
                git_url=payload["git_url"].strip(),
                branch=payload["branch"].strip(),
                before_commit=payload["before_commit"].strip(),
                after_commit=payload["after_commit"].strip(),
                project_name=_analysis_project_name(payload),
                provider_id=payload["provider_id"].strip(),
                call_graph_depth=_analysis_call_graph_depth(payload),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            self._send_json({"error": "invalid_request"}, status=400)
            return

        job = self.server.job_store.create(request)
        if self.server.analyzer is None:
            self._send_json({"job": job.to_dict()}, status=202)
            return

        if self.server.execute_async:
            job = self.server.job_store.start(job.id)
            thread = threading.Thread(target=self._run_analysis_job, args=(job.id, request), daemon=True)
            thread.start()
            self._send_json({"job": job.to_dict()}, status=202)
            return

        try:
            job = self.server.job_store.start(job.id)
            result = _analyze_with_progress(
                self.server.analyzer,
                request,
                lambda stage: self.server.job_store.add_progress(job.id, stage),
            )
            job = self.server.job_store.complete(job.id, _analysis_result_to_dict(result))
            self._send_json({"job": job.to_dict()}, status=200)
        except Exception as error:
            job = self.server.job_store.fail(job.id, str(error))
            self._send_json({"job": job.to_dict()}, status=500)

    def _run_analysis_job(self, job_id: str, request: ImpactAnalysisRequest) -> None:
        try:
            result = _analyze_with_progress(
                self.server.analyzer,
                request,
                lambda stage: self.server.job_store.add_progress(job_id, stage),
            )
            self.server.job_store.complete(job_id, _analysis_result_to_dict(result))
        except Exception as error:
            self.server.job_store.fail(job_id, str(error))

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(content_length).decode("utf-8"))

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _dashboard_html() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Impact Analysis AI</title>
  <style>
    :root {
      color-scheme: light;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      color: #172033;
      background: #f4f7fb;
      --panel: #ffffff;
      --line: #d9e1ec;
      --muted: #667085;
      --ink: #172033;
      --teal: #0f8f83;
      --indigo: #3957d7;
      --coral: #c65d3d;
      --gold: #a76b14;
      --soft-teal: #e7f6f4;
      --soft-indigo: #edf1ff;
      --soft-coral: #fff1eb;
      --shadow: 0 16px 36px rgba(28, 40, 68, 0.08);
    }
    body { margin: 0; }
    header {
      background: #ffffff;
      border-bottom: 1px solid var(--line);
      padding: 22px clamp(18px, 4vw, 46px);
      position: sticky;
      top: 0;
      z-index: 3;
    }
    main {
      display: block;
      padding: 22px clamp(18px, 4vw, 46px) 36px;
      max-width: 1480px;
      margin: 0 auto;
    }
    h1 { font-size: 26px; margin: 0; letter-spacing: 0; }
    h2 { margin: 0; font-size: 18px; }
    h3 { margin: 0; font-size: 15px; }
    p { margin: 0; }
    .topbar {
      display: flex;
      justify-content: space-between;
      gap: 18px;
      align-items: center;
      max-width: 1480px;
      margin: 0 auto;
    }
    .subtitle { margin-top: 6px; color: var(--muted); font-size: 14px; }
    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      min-height: 32px;
      padding: 0 12px;
      border-radius: 999px;
      background: var(--soft-indigo);
      color: #263e9c;
      font-weight: 700;
      white-space: nowrap;
    }
    .dot { width: 8px; height: 8px; border-radius: 999px; background: #0f9f6e; }
    .stack { display: grid; gap: 18px; align-content: start; }
    .content { display: grid; gap: 18px; align-content: start; min-width: 0; }
    .tabs {
      display: flex;
      gap: 8px;
      margin: 0 0 18px;
      overflow-x: auto;
      padding-bottom: 4px;
    }
    .tab-button {
      flex: 0 0 auto;
      min-height: 38px;
      background: #ffffff;
      color: #344054;
      border: 1px solid var(--line);
      box-shadow: none;
    }
    .tab-button.active {
      background: var(--indigo);
      border-color: var(--indigo);
      color: #ffffff;
    }
    .tab-panel { display: none; }
    .tab-panel.active { display: grid; gap: 18px; }
    .home-grid {
      display: grid;
      grid-template-columns: minmax(320px, 430px) minmax(0, 1fr);
      gap: 18px;
      align-items: start;
    }
    .quick-actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      margin-top: 14px;
    }
    section {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      overflow: hidden;
    }
    .section-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      background: #fbfcfe;
    }
    .section-body { padding: 18px; }
    .accent {
      color: var(--coral);
      font-size: 13px;
      font-weight: 750;
    }
    label, input, select, button { font: inherit; }
    form {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    label { display: grid; gap: 6px; font-size: 13px; font-weight: 700; color: #344054; }
    input, select, textarea {
      border: 1px solid #c8d1df;
      border-radius: 6px;
      padding: 0 10px;
      background: #ffffff;
      color: var(--ink);
    }
    input:focus, select:focus, textarea:focus {
      outline: 2px solid rgba(8, 127, 140, 0.18);
      border-color: var(--teal);
    }
    input, select { min-height: 40px; }
    textarea {
      min-height: 140px;
      padding: 10px;
      resize: vertical;
      line-height: 1.45;
    }
    button {
      align-self: end;
      border: 0;
      border-radius: 6px;
      min-height: 40px;
      padding: 0 16px;
      color: #ffffff;
      background: var(--indigo);
      cursor: pointer;
      font-weight: 750;
    }
    button:hover { filter: brightness(0.96); }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { border-bottom: 1px solid #edf1f6; padding: 11px 12px; text-align: left; vertical-align: top; }
    th { color: #475467; background: #fbfcfe; font-weight: 750; }
    .table-wrap { overflow-x: auto; }
    .muted { color: var(--muted); }
    .wide { grid-column: 1 / -1; }
    .actions { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .secondary {
      background: #ffffff;
      color: var(--indigo);
      border: 1px solid #bac6ff;
    }
    .danger-soft {
      background: var(--soft-coral);
      color: #9e3f21;
      border: 1px solid #f2c3b3;
    }
    .toolbar {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
    .search-input {
      min-width: min(360px, 100%);
      flex: 1 1 280px;
    }
    .analysis-list {
      display: grid;
      gap: 12px;
      padding: 16px;
    }
    .analysis-card {
      display: grid;
      grid-template-columns: minmax(180px, 1.1fr) minmax(180px, 1fr) minmax(220px, 1.25fr) auto;
      gap: 14px;
      align-items: center;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      padding: 14px;
    }
    .analysis-main {
      display: grid;
      gap: 6px;
      min-width: 0;
    }
    .analysis-title {
      display: flex;
      gap: 8px;
      align-items: center;
      min-width: 0;
      font-weight: 800;
    }
    .analysis-title span:first-child,
    .summary-line {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .commit-range-compact {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      color: #344054;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      white-space: nowrap;
    }
    .status-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 24px;
      padding: 0 8px;
      border-radius: 999px;
      background: var(--soft-indigo);
      color: #263e9c;
      font-size: 12px;
      font-weight: 800;
    }
    .status-badge.completed { background: var(--soft-teal); color: #08796f; }
    .status-badge.failed { background: var(--soft-coral); color: #a14528; }
    .analysis-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
    }
    .analysis-actions {
      display: flex;
      justify-content: flex-end;
    }
    .metric-row {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
    }
    .metric {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #ffffff;
    }
    .metric strong { display: block; font-size: 20px; margin-top: 4px; }
    .badge {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 0 8px;
      border-radius: 999px;
      background: var(--soft-teal);
      color: #075f69;
      font-size: 12px;
      font-weight: 750;
    }
    .detail-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
      gap: 14px;
    }
    .detail-block {
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 12px;
      background: #ffffff;
    }
    .detail-block h3 { margin: 0 0 8px; font-size: 15px; }
    .detail-block ul { margin: 0; padding-left: 18px; }
    .report-layout {
      display: grid;
      gap: 16px;
    }
    .report-hero {
      display: grid;
      gap: 12px;
      border: 1px solid #cdd7ff;
      border-radius: 8px;
      background: linear-gradient(180deg, #f8faff 0%, #ffffff 100%);
      padding: 18px;
    }
    .report-title-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      flex-wrap: wrap;
    }
    .report-title-row h3 { font-size: 20px; margin: 0; }
    .report-actions { display: flex; gap: 10px; flex-wrap: wrap; }
    .report-summary {
      color: #344054;
      line-height: 1.7;
      max-width: 980px;
    }
    .report-stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
    }
    .report-stat {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      padding: 12px;
    }
    .report-stat span { color: var(--muted); font-size: 12px; }
    .report-stat strong { display: block; margin-top: 4px; font-size: 18px; }
    .report-section {
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #ffffff;
      overflow: hidden;
    }
    .report-section-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: center;
      padding: 14px 16px;
      background: #fbfcff;
      border-bottom: 1px solid var(--line);
    }
    .report-section-head h3 { font-size: 16px; margin: 0; }
    .feature-group {
      display: grid;
      gap: 10px;
      padding: 14px 16px;
      border-bottom: 1px solid #edf1f6;
    }
    .feature-group:last-child { border-bottom: 0; }
    .feature-title {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      font-weight: 800;
    }
    .finding-list {
      display: grid;
      gap: 8px;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .finding-item {
      border: 1px solid #edf1f6;
      border-radius: 8px;
      background: #ffffff;
      padding: 10px 12px;
      line-height: 1.55;
    }
    .test-tag-card {
      display: grid;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid #edf1f6;
    }
    .test-tag-card:last-child { border-bottom: 0; }
    .test-tag-summary {
      border-left: 4px solid var(--indigo);
      background: #f7f9ff;
      color: #344054;
      padding: 10px 12px;
      border-radius: 6px;
      line-height: 1.6;
    }
    .test-tag-section {
      display: grid;
      gap: 8px;
    }
    .test-tag-section h4 {
      margin: 0;
      color: #475467;
      font-size: 13px;
    }
    .test-tag-list {
      margin: 0;
      padding-left: 20px;
      line-height: 1.75;
    }
    .test-chip-row {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .test-chip {
      display: inline-flex;
      align-items: center;
      min-height: 26px;
      padding: 0 9px;
      border: 1px solid #d8def8;
      border-radius: 999px;
      background: #f8faff;
      color: #344054;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }
    .finding-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-bottom: 6px;
      color: var(--muted);
      font-size: 12px;
    }
    .impact-badge {
      display: inline-flex;
      align-items: center;
      min-height: 24px;
      padding: 0 8px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 800;
      background: var(--soft-indigo);
      color: #263e9c;
    }
    .impact-badge.critical { background: #fff0f0; color: #b42318; }
    .impact-badge.high { background: #fff1eb; color: #b54708; }
    .impact-badge.medium { background: #fff8e6; color: #8a6100; }
    .impact-badge.low { background: #e7f6f4; color: #08796f; }
    .markdown-preview {
      white-space: pre-wrap;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      line-height: 1.6;
      max-height: 260px;
      overflow: auto;
      background: #0f172a;
      color: #e5e7eb;
      border-radius: 8px;
      padding: 12px;
    }
    .log-list {
      display: grid;
      gap: 8px;
      padding: 14px 16px;
    }
    .log-entry {
      border: 1px solid #edf1f6;
      border-radius: 8px;
      background: #ffffff;
      overflow: hidden;
    }
    .log-entry summary {
      cursor: pointer;
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      padding: 10px 12px;
      font-weight: 800;
      background: #fbfcff;
    }
    .log-entry.error summary {
      background: #fff7f4;
      color: #a14528;
    }
    .log-body {
      display: grid;
      gap: 8px;
      padding: 10px 12px;
      color: #344054;
      line-height: 1.55;
    }
    .log-detail {
      white-space: pre-wrap;
      overflow: auto;
      max-height: 320px;
      border-radius: 8px;
      background: #101828;
      color: #e4e7ec;
      padding: 10px;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }
    .diff-details {
      border: 1px solid #edf1f6;
      border-radius: 8px;
      background: #ffffff;
      overflow: hidden;
    }
    .diff-details + .diff-details { margin-top: 10px; }
    .diff-details summary {
      cursor: pointer;
      padding: 12px 14px;
      font-weight: 800;
      color: #344054;
      background: #fbfcff;
    }
    .diff-pre {
      margin: 0;
      padding: 12px 14px;
      max-height: 360px;
      overflow: auto;
      background: #101828;
      color: #e4e7ec;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
      line-height: 1.6;
      white-space: pre;
    }
    .call-tree {
      display: grid;
      gap: 12px;
    }
    .call-function-details,
    .call-file-details {
      border: 1px solid #edf1f6;
      border-radius: 8px;
      background: #ffffff;
      overflow: hidden;
    }
    .call-function-details summary,
    .call-file-details summary {
      cursor: pointer;
      padding: 12px 14px;
      font-weight: 800;
      color: #344054;
      background: #fbfcff;
    }
    .call-direction-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      padding: 14px;
    }
    .call-direction {
      border: 1px solid #edf1f6;
      border-radius: 8px;
      background: #ffffff;
      overflow: hidden;
    }
    .call-direction-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      padding: 10px 12px;
      border-bottom: 1px solid #edf1f6;
      background: #f8fafc;
      font-weight: 800;
    }
    .call-file-list {
      display: grid;
      gap: 8px;
      padding: 10px;
    }
    .call-target-list {
      margin: 0;
      padding: 10px 14px 12px 28px;
      line-height: 1.7;
      font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
      font-size: 12px;
    }
    .call-target-meta {
      color: var(--muted);
      font-family: Inter, system-ui, sans-serif;
      font-size: 12px;
      margin-left: 6px;
    }
    .model-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      margin-bottom: 14px;
    }
    .provider-note {
      padding: 10px 12px;
      border-radius: 8px;
      background: #fff8e6;
      border: 1px solid #f1d38b;
      color: #72520f;
      font-size: 13px;
      line-height: 1.45;
    }
    .progress-cell {
      min-width: 190px;
    }
    .progress-line {
      display: grid;
      gap: 6px;
    }
    progress {
      width: 100%;
      height: 9px;
      overflow: hidden;
      border: 0;
      border-radius: 999px;
      background: #e9eef5;
    }
    progress::-webkit-progress-bar {
      border-radius: 999px;
      background: #e9eef5;
    }
    progress::-webkit-progress-value {
      border-radius: 999px;
      background: var(--teal);
    }
    progress::-moz-progress-bar {
      border-radius: 999px;
      background: var(--teal);
    }
    .progress-meta {
      display: flex;
      justify-content: space-between;
      gap: 8px;
      color: var(--muted);
      font-size: 12px;
    }
    @media (max-width: 980px) {
      .home-grid { grid-template-columns: 1fr; }
      .analysis-card { grid-template-columns: 1fr; align-items: stretch; }
      .analysis-actions { justify-content: flex-start; }
      .report-stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .call-direction-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 640px) {
      form, .model-grid, .metric-row { grid-template-columns: 1fr; }
      .report-stats { grid-template-columns: 1fr; }
      header { position: static; }
      .topbar { align-items: flex-start; flex-direction: column; }
    }
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <div>
        <h1>Impact Analysis AI</h1>
        <p class="subtitle">代码改动影响面分析、AI 评审规范和测试用例生成看板</p>
      </div>
      <div class="status-pill"><span class="dot"></span><span>服务运行中</span></div>
    </div>
  </header>
  <main>
    <nav class="tabs" aria-label="主功能标签">
      <button type="button" class="tab-button active" data-tab-target="home-panel">首页</button>
      <button type="button" class="tab-button" data-tab-target="analysis-panel">新建分析</button>
      <button type="button" class="tab-button" data-tab-target="profile-panel">业务说明</button>
      <button type="button" class="tab-button" data-tab-target="model-panel">模型与 API 配置</button>
      <button type="button" class="tab-button" data-tab-target="history-panel">分析历史</button>
      <button type="button" class="tab-button" data-tab-target="detail-panel">分析详情</button>
      <button type="button" class="tab-button" data-tab-target="standard-panel">评审规范</button>
    </nav>

    <div id="home-panel" class="tab-panel active">
      <section>
        <div class="section-head">
          <div><h2>首页</h2><p class="subtitle">从这里进入分析、查看历史、配置模型和检查运行状态</p></div>
          <button type="button" class="secondary" id="refresh-analyses">刷新</button>
        </div>
        <div class="section-body">
          <div class="metric-row">
            <div class="metric"><span class="muted">历史任务</span><strong id="metric-total">0</strong></div>
            <div class="metric"><span class="muted">已完成</span><strong id="metric-completed">0</strong></div>
            <div class="metric"><span class="muted">运行中/排队</span><strong id="metric-active">0</strong></div>
          </div>
          <div class="quick-actions">
            <button type="button" data-tab-target="analysis-panel">新建分析</button>
            <button type="button" class="secondary" data-tab-target="history-panel">查看分析历史</button>
            <button type="button" class="secondary" data-tab-target="profile-panel">业务说明</button>
            <button type="button" class="secondary" data-tab-target="model-panel">模型与 API 配置</button>
          </div>
          <p class="subtitle">报告结果将以中文生成，包含影响面摘要、评审发现、调用链和测试用例。</p>
        </div>
      </section>
    </div>

    <div id="analysis-panel" class="tab-panel">
      <section>
        <div class="section-head">
          <div><h2>新建分析</h2><p class="subtitle">填写 Git 信息后提交异步分析任务，调用链深度默认 2 层，可按需调整</p></div>
          <span class="accent">Impact Review</span>
        </div>
        <div class="section-body">
          <form id="analysis-form">
            <label class="wide">Git 地址<input name="git_url" placeholder="https://github.com/Kong/kong.git"></label>
            <label>分支<input name="branch" placeholder="main"></label>
            <label>项目名称<input name="project_name" placeholder="Kong-kong"></label>
            <label>修改前 Commit<input name="before_commit" placeholder="abc123"></label>
            <label>修改后 Commit<input name="after_commit" placeholder="def456"></label>
            <label>调用链深度<input name="call_graph_depth" type="number" min="1" max="5" step="1" value="2"></label>
            <label class="wide">AI 模型服务<select id="provider-select" name="provider_id"></select></label>
            <div class="actions wide">
              <button type="button" id="submit-analysis">开始分析</button>
              <span class="muted">会提取改动函数、按配置深度分析调用链路，并生成中文报告。</span>
            </div>
          </form>
        </div>
      </section>
    </div>

    <div id="profile-panel" class="tab-panel">
      <section>
        <div class="section-head">
          <div><h2>业务说明</h2><p class="subtitle">每个项目可维护一份 business.md，项目名称可从 Git 地址自动推导</p></div>
        </div>
        <div class="section-body">
          <form id="profile-form">
            <label class="wide">Git 地址<input name="git_url" placeholder="https://github.com/Kong/kong.git"></label>
            <label class="wide">项目名称<input name="project_name" placeholder="Kong-kong"></label>
            <label class="wide">business.md<textarea name="business_context" placeholder="# 业务背景&#10;&#10;退款必须记录审计日志。"></textarea></label>
            <div class="actions wide">
              <button type="button" class="secondary" id="load-profile">读取说明</button>
              <button type="button" id="save-profile">保存说明</button>
              <span id="profile-status" class="muted"></span>
            </div>
          </form>
        </div>
      </section>
    </div>

    <div id="model-panel" class="tab-panel">
      <section>
        <div class="section-head">
          <div><h2>模型与 API 配置</h2><p class="subtitle">配置模型名、API Key、Base URL，并查看供应商接口说明</p></div>
        </div>
        <div class="section-body">
          <form id="model-config-form">
            <label class="wide">默认分析模型<select id="default-provider-select" name="default_provider_id"></select></label>
            <label class="wide">模型服务<select id="model-provider-select" name="provider_id"></select></label>
            <label class="wide">模型名称<input name="model" placeholder="deepseek-chat"></label>
            <label class="wide">Base URL<input name="base_url" placeholder="https://api.deepseek.com/v1"></label>
            <label class="wide">API Key<input name="api_key" type="text" placeholder="sk-..."></label>
            <div class="actions wide">
              <button type="button" class="secondary" id="use-default-model">使用供应商默认模型</button>
              <button type="button" class="secondary" id="set-default-provider">设为默认分析模型</button>
              <button type="button" id="save-model-config">保存模型配置</button>
              <button type="button" class="secondary" id="test-model-config">测试模型</button>
              <span id="model-config-status" class="muted"></span>
            </div>
          </form>
          <div class="provider-note">API Key 会显示当前已保存配置，方便检查和修改；也可以继续使用环境变量作为默认配置。</div>
          <h3>API 配置说明</h3>
          <div class="detail-grid">
            <div class="detail-block"><h3>模型配置接口</h3><p>读取：GET /api/model-configs</p><p>保存：PUT /api/model-configs/{provider_id}</p><p>默认：POST /api/model-configs/default</p><p>测试：POST /api/model-configs/{provider_id}/test</p></div>
            <div class="detail-block"><h3>分析任务接口</h3><p>创建：POST /api/analyses</p><p>列表：GET /api/analyses</p><p>详情：GET /api/analyses/{job_id}</p></div>
            <div class="detail-block"><h3>配置说明</h3><p>模型名称、Base URL 和 API Key 可以通过页面保存，也可以通过环境变量提供。页面配置优先于默认值。</p></div>
          </div>
          <div class="table-wrap">
            <table>
              <thead><tr><th>名称</th><th>默认</th><th>当前模型</th><th>供应商默认模型</th><th>当前密钥</th><th>模型环境变量</th><th>密钥环境变量</th><th>Base URL 环境变量</th><th>区域</th></tr></thead>
              <tbody id="provider-table"><tr><td class="muted" colspan="9">加载中</td></tr></tbody>
            </table>
          </div>
        </div>
      </section>
    </div>

    <div id="history-panel" class="tab-panel">
      <section>
        <div class="section-head">
          <div><h2>分析历史</h2><p class="subtitle">支持按项目、分支、状态、Commit、摘要和错误搜索</p></div>
          <div class="toolbar">
            <label>历史搜索<input id="history-search" class="search-input" placeholder="输入项目、状态、commit 或关键字"></label>
            <button type="button" class="secondary" id="refresh-history">刷新历史</button>
          </div>
        </div>
        <div id="analysis-list" class="analysis-list"><p class="muted">加载中</p></div>
      </section>
    </div>

    <div id="detail-panel" class="tab-panel">
      <section>
        <div class="section-head">
          <div><h2>分析详情</h2><p class="subtitle">单独查看调用链路、结构化评审发现、结构化测试用例和 Markdown 报告</p></div>
          <button type="button" class="secondary" data-tab-target="history-panel">返回历史</button>
        </div>
        <div class="section-body">
          <div id="analysis-detail" class="muted">选择一条已完成的分析结果，查看影响范围、评审建议、生成的测试用例和 Token 用量。</div>
        </div>
      </section>
    </div>

    <div id="standard-panel" class="tab-panel">
      <section>
        <div class="section-head">
          <div><h2>评审规范</h2><p class="subtitle">按语言维护默认评审标准，分析报告会按这些标准生成中文评审项</p></div>
        </div>
        <div class="section-body">
          <form id="review-standard-form">
            <label>语言<input name="language" placeholder="lua"></label>
            <label class="wide">评审规范 JSON<textarea name="sections" placeholder='{"正确性":["检查返回值和调用方契约。"],"语言专项":["检查协程、元表和 require 缓存。"]}'></textarea></label>
            <div class="actions wide">
              <button type="button" class="secondary" id="load-review-standard">读取评审规范</button>
              <button type="button" id="save-review-standard">保存评审规范</button>
              <span id="review-standard-status" class="muted"></span>
            </div>
          </form>
        </div>
        <div class="table-wrap">
          <table>
            <thead><tr><th>语言</th><th>规范分组</th><th>语言专项检查</th></tr></thead>
            <tbody id="review-standards-table"><tr><td class="muted" colspan="3">加载中</td></tr></tbody>
          </table>
        </div>
      </section>
    </div>
  </main>
  <script>
    const form = document.querySelector('#analysis-form');
    const profileForm = document.querySelector('#profile-form');
    const modelConfigForm = document.querySelector('#model-config-form');
    const projectInput = form.querySelector('[name="project_name"]');
    const gitUrlInput = form.querySelector('[name="git_url"]');
    const profileGitUrlInput = profileForm.querySelector('[name="git_url"]');
    const profileProjectInput = profileForm.querySelector('[name="project_name"]');
    const profileContextInput = profileForm.querySelector('[name="business_context"]');
    const profileStatus = document.querySelector('#profile-status');
    const modelConfigStatus = document.querySelector('#model-config-status');
    const reviewStandardForm = document.querySelector('#review-standard-form');
    const reviewStandardStatus = document.querySelector('#review-standard-status');
    const historySearch = document.querySelector('#history-search');
    const encodeProject = (value) => encodeURIComponent(value.trim());
    const defaultProjectNameFromGitUrl = (value) => {
      let text = String(value || '').trim().split('?')[0].split('#')[0].replace(/[/]+$/, '');
      if (!text) return '';
      if (text.endsWith('.git')) text = text.slice(0, -4);
      if (!text.includes('://') && text.includes(':') && !text.split(':', 1)[0].includes('/')) {
        text = text.replace(':', '/');
      }
      const parts = text.split('/').filter(Boolean);
      if (parts.length === 0) return '';
      return parts.slice(Math.max(0, parts.length - 2)).join('-');
    };
    const fillProjectNameFromGitUrl = (gitInput, projectNameInput, { overwrite = false } = {}) => {
      const derived = defaultProjectNameFromGitUrl(gitInput.value);
      const userEdited = projectNameInput.dataset.userEdited === 'true';
      if (derived && (overwrite || !userEdited || !projectNameInput.value.trim())) {
        projectNameInput.value = derived;
        projectNameInput.dataset.userEdited = 'false';
      }
      return derived;
    };
    const markProjectNameEdited = (projectNameInput) => {
      projectNameInput.dataset.userEdited = 'true';
    };
    let latestJobs = [];
    let modelConfigs = [];
    let defaultProviderId = '';
    const showTab = (panelId) => {
      document.querySelectorAll('.tab-panel').forEach((panel) => panel.classList.toggle('active', panel.id === panelId));
      document.querySelectorAll('.tab-button').forEach((button) => button.classList.toggle('active', button.dataset.tabTarget === panelId));
      window.scrollTo({ top: 0, behavior: 'smooth' });
    };
    document.querySelectorAll('[data-tab-target]').forEach((button) => {
      button.addEventListener('click', () => showTab(button.dataset.tabTarget));
    });
    const escapeHtml = (value) => String(value ?? '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;')
      .replaceAll("'", '&#39;');
    const shortCommit = (value) => {
      const text = String(value || '').trim();
      return text.length > 10 ? text.slice(0, 10) : text;
    };
    const renderList = (items) => {
      if (!items || items.length === 0) {
        return '<p class="muted">暂无</p>';
      }
      return `<ul>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}</ul>`;
    };
    const callNameParts = (value) => {
      const text = String(value || '');
      const pathMatch = text.match(/^(.+):(\\d+)$/);
      if (pathMatch) {
        const filePath = pathMatch[1];
        return {
          filePath,
          functionName: `${filePath.split('/').pop()}:${pathMatch[2]}`,
          meta: `第 ${pathMatch[2]} 行`
        };
      }
      const segments = text.split(/[.:]/).filter(Boolean);
      return {
        filePath: '图谱函数',
        functionName: segments.slice(-1)[0] || text || '未知调用',
        meta: text
      };
    };
    const inferCallFile = (call, sourceFunction, changedFunctions) => {
      const parsed = callNameParts(call);
      if (parsed.filePath !== '图谱函数') return parsed.filePath;
      const source = (changedFunctions || []).find((fn) => fn.qualified_name === sourceFunction);
      if (source) {
        const moduleName = String(source.file_path || '').split('/').pop().replace(/\\.[^.]+$/, '');
        if (moduleName && String(call).includes(moduleName)) {
          return source.file_path;
        }
      }
      return parsed.filePath;
    };
    const groupCallsByFile = (calls, sourceFunction, changedFunctions) => {
      const groups = new Map();
      const seen = new Set();
      (calls || []).forEach((call) => {
        const filePath = inferCallFile(call, sourceFunction, changedFunctions);
        const parsed = callNameParts(call);
        const key = `${filePath}::${parsed.functionName}`;
        if (seen.has(key)) return;
        seen.add(key);
        if (!groups.has(filePath)) groups.set(filePath, []);
        groups.get(filePath).push(call);
      });
      return Array.from(groups.entries()).map(([filePath, entries]) => ({ filePath, entries }));
    };
    const renderCallFileGroups = (calls, sourceFunction, changedFunctions) => {
      const groups = groupCallsByFile(calls, sourceFunction, changedFunctions);
      if (groups.length === 0) return '<p class="muted">暂无调用函数</p>';
      return `<div class="call-file-list">${groups.map((group) => `<details class="call-file-details" open>
        <summary>${escapeHtml(group.filePath)} · ${group.entries.length} 个调用</summary>
        <ul class="call-target-list">${group.entries.map((call) => {
          const parsed = callNameParts(call);
          return `<li><strong>${escapeHtml(parsed.functionName)}</strong><span class="call-target-meta">${escapeHtml(parsed.meta)}</span></li>`;
        }).join('')}</ul>
      </details>`).join('')}</div>`;
    };
    const renderCallGraphTree = (graph, functions) => {
      const changedFunctions = functions || [];
      if (changedFunctions.length === 0) {
        return '<div class="call-tree"><p class="muted">暂无改动函数</p></div>';
      }
      return `<div class="call-tree">${changedFunctions.map((fn) => {
        const source = fn.qualified_name || '未命名函数';
        const inbound = ((graph || {}).inbound || {})[source] || [];
        const outbound = ((graph || {}).outbound || {})[source] || [];
        return `<details class="call-function-details" open>
          <summary>${escapeHtml(fn.file_path || '未知文件')} · ${escapeHtml(source)}</summary>
          <div class="call-direction-grid">
            <div class="call-direction">
              <div class="call-direction-head"><span>上游调用方</span><span class="muted">${inbound.length} 个</span></div>
              ${renderCallFileGroups(inbound, source, changedFunctions)}
            </div>
            <div class="call-direction">
              <div class="call-direction-head"><span>下游被调方</span><span class="muted">${outbound.length} 个</span></div>
              ${renderCallFileGroups(outbound, source, changedFunctions)}
            </div>
          </div>
        </details>`;
      }).join('')}</div>`;
    };
    const renderStructuredItems = (items) => {
      if (!items || items.length === 0) {
        return '<p class="muted">暂无</p>';
      }
      return `<ul>${items.map((item) => {
        const entries = Object.entries(item || {})
          .filter(([_key, value]) => value !== undefined && value !== null && value !== '')
          .map(([key, value]) => `${escapeHtml(key)}: ${escapeHtml(Array.isArray(value) ? value.join(', ') : value)}`);
        return `<li>${entries.join('<br>')}</li>`;
      }).join('')}</ul>`;
    };
    const normalizeImpactLevel = (value) => {
      const text = String(value || '').toLowerCase();
      if (['critical', 'high', 'medium', 'low'].includes(text)) {
        return text;
      }
      if (text.includes('严重') || text.includes('critical')) return 'critical';
      if (text.includes('高') || text.includes('high')) return 'high';
      if (text.includes('中') || text.includes('medium')) return 'medium';
      return 'low';
    };
    const impactLevelLabel = (level) => ({
      critical: '严重影响',
      high: '高影响',
      medium: '中影响',
      low: '低影响'
    }[normalizeImpactLevel(level)]);
    const itemImpactLevel = (item) => normalizeImpactLevel(item.impact_level || item.severity);
    const itemBusinessFeature = (item) => {
      if (item.business_feature || item.feature || item.domain) {
        return item.business_feature || item.feature || item.domain;
      }
      const raw = item.function || item.target || item.name || '未分类业务功能';
      const first = String(raw).split(/[./:]/).filter(Boolean)[0];
      return first || '未分类业务功能';
    };
    const itemBusinessSubfeature = (item) => item.business_subfeature || item.subfeature || item.capability || item.name || '未命名业务小功能';
    const bilingualLabel = (zh, en) => {
      const main = String(zh || '').trim();
      const english = String(en || '').trim();
      if (main && english && !main.includes(`(${english})`)) return `${main} (${english})`;
      return main || english || '';
    };
    const arrayFrom = (value) => {
      if (Array.isArray(value)) return value.filter(Boolean);
      if (typeof value === 'string' && value.trim()) return [value.trim()];
      return [];
    };
    const uniqueItems = (items) => Array.from(new Set((items || []).filter(Boolean)));
    const groupByBusinessFeature = (items) => {
      const groups = new Map();
      (items || []).forEach((item) => {
        const feature = itemBusinessFeature(item || {});
        if (!groups.has(feature)) {
          groups.set(feature, []);
        }
        groups.get(feature).push(item || {});
      });
      return Array.from(groups.entries()).map(([feature, entries]) => ({ feature, entries }));
    };
    const renderImpactBadge = (level) => {
      const normalized = normalizeImpactLevel(level);
      return `<span class="impact-badge ${normalized}">${impactLevelLabel(normalized)}</span>`;
    };
    const renderGroupedReviewFindings = (items) => {
      const groups = groupByBusinessFeature(items);
      if (groups.length === 0) return '<div class="feature-group"><p class="muted">暂无评审发现</p></div>';
      return groups.map((group) => `<div class="feature-group">
        <div class="feature-title"><span>业务大类：${escapeHtml(group.feature)}</span><span class="muted">${group.entries.length} 条发现</span></div>
        <ul class="finding-list">${group.entries.map((item) => `<li class="finding-item">
          <div class="finding-meta">${renderImpactBadge(itemImpactLevel(item))}<span>${escapeHtml(item.standard || '评审')}</span><span>${escapeHtml(item.function || '')}</span></div>
          <strong>业务小功能：${escapeHtml(itemBusinessSubfeature(item))}</strong>
          <div>${escapeHtml(item.finding || item.message || item.summary || '')}</div>
          ${(item.impacted_callers || []).length ? `<div class="muted">影响调用方：${escapeHtml(item.impacted_callers.join(', '))}</div>` : ''}
        </li>`).join('')}</ul>
      </div>`).join('');
    };
    const renderGroupedTestCases = (items) => {
      const groups = groupByBusinessFeature(items);
      if (groups.length === 0) return '<div class="feature-group"><p class="muted">暂无测试用例</p></div>';
      return groups.map((group) => {
        const flows = uniqueItems(group.entries.flatMap((item) => {
          const explicit = arrayFrom(item.affected_business_flows);
          const fallback = bilingualLabel(item.business_subfeature || itemBusinessSubfeature(item), item.business_subfeature_en);
          return explicit.length ? explicit : [fallback];
        }));
        const changedSymbols = uniqueItems(group.entries.flatMap((item) => [
          ...arrayFrom(item.changed_symbols),
          item.target,
        ]));
        const affectedFiles = uniqueItems(group.entries.flatMap((item) => arrayFrom(item.affected_files)));
        const tags = uniqueItems(group.entries.flatMap((item) => arrayFrom(item.test_tags)));
        const summary = group.entries.flatMap((item) => arrayFrom(item.covers))[0]
          || `围绕 ${group.feature} 的改动影响生成回归测试标签，覆盖调用链相关业务流程。`;
        return `<div class="test-tag-card">
          <div class="feature-title"><span>${escapeHtml(group.feature)}</span><span class="muted">${group.entries.length} 个回归用例</span></div>
          <div class="test-tag-summary">${escapeHtml(summary)}</div>
          <div class="test-tag-section">
            <h4>受影响功能点（${flows.length}）</h4>
            <ul class="test-tag-list">${flows.map((flow) => `<li>${escapeHtml(flow)}</li>`).join('')}</ul>
          </div>
          <div class="test-tag-section">
            <h4>回归测试清单（${group.entries.length}）</h4>
            <ul class="test-tag-list">${group.entries.map((item, index) => {
              const id = item.test_id || `TC${String(index + 1).padStart(3, '0')}`;
              const goal = item.verification_goal || arrayFrom(item.covers)[0] || item.name || 'verify impacted behavior';
              return `<li>${escapeHtml(id)}: ${escapeHtml(item.name || itemBusinessSubfeature(item))} -- ${escapeHtml(goal)}</li>`;
            }).join('')}</ul>
          </div>
          <div class="test-tag-section">
            <h4>变更符号</h4>
            <div class="test-chip-row">${(changedSymbols.length ? changedSymbols : ['暂无']).map((symbol) => `<span class="test-chip">${escapeHtml(symbol)}</span>`).join('')}</div>
          </div>
          <div class="test-tag-section">
            <h4>测试标签</h4>
            <div class="test-chip-row">${(tags.length ? tags : group.entries.map((item) => item.type || 'regression')).map((tag) => `<span class="test-chip">${escapeHtml(tag)}</span>`).join('')}</div>
          </div>
          <div class="test-tag-section">
            <h4>涉及文件（${affectedFiles.length}）</h4>
            <ul class="test-tag-list">${(affectedFiles.length ? affectedFiles : ['暂无明确文件']).map((file) => `<li>${escapeHtml(file)}</li>`).join('')}</ul>
          </div>
        </div>`;
      }).join('');
    };
    const enrichTestCasesWithChangeContext = (items, changedFunctions) => {
      const files = uniqueItems((changedFunctions || []).map((fn) => fn.file_path));
      const symbols = uniqueItems((changedFunctions || []).map((fn) => fn.qualified_name));
      return (items || []).map((item) => ({
        ...item,
        affected_files: arrayFrom(item.affected_files).length ? item.affected_files : files,
        changed_symbols: arrayFrom(item.changed_symbols).length ? item.changed_symbols : symbols,
      }));
    };
    const callGraphStatusInfo = (job) => {
      const graph = job && job.result && job.result.call_graph ? job.result.call_graph : {};
      const statuses = Object.values(graph.trace_status || {});
      if (!job || !job.result) {
        return { label: '未执行', detail: '尚未生成分析结果' };
      }
      if (statuses.length === 0) {
        return { label: '未记录', detail: '旧历史或无改动函数未记录链路状态' };
      }
      if (statuses.every((status) => status === 'success')) {
        return { label: '成功', detail: `DeusData/codebase-memory-mcp 已完成 ${((job.request || {}).call_graph_depth || (graph || {}).depth || 2)} 层调用链路获取` };
      }
      if (statuses.some((status) => status === 'augmented_success')) {
        return { label: '图谱成功，源码补充', detail: 'codebase-memory-mcp 已返回链路，部分 Lua 别名调用由当前仓库源码扫描补齐' };
      }
      if (statuses.some((status) => status === 'fallback_success')) {
        return { label: '源码降级成功', detail: 'codebase-memory-mcp 未返回完整链路，已使用当前仓库源码扫描补齐调用关系' };
      }
      if (statuses.some((status) => status === 'index_failed')) {
        return { label: '索引失败', detail: 'codebase-memory-mcp 索引未生成可用知识图谱，源码降级也没有找到调用关系' };
      }
      if (statuses.some((status) => status === 'partial')) {
        return { label: '部分成功', detail: '部分方向获取成功，部分方向缺失' };
      }
      if (statuses.some((status) => status === 'not_found')) {
        return { label: '函数未入图', detail: 'codebase-memory-mcp 未在知识图谱中找到部分改动函数' };
      }
      return { label: '未知', detail: statuses.join(', ') };
    };
    const renderCallGraphStatus = (job) => {
      const info = callGraphStatusInfo(job);
      return `DeusData/codebase-memory-mcp 调用链：${info.label}`;
    };
    const renderDiffHunks = (functions) => {
      if (!functions || functions.length === 0) {
        return '<div class="feature-group"><p class="muted">暂无 Git Diff</p></div>';
      }
      return `<div class="feature-group">${functions.map((fn) => `<details class="diff-details">
        <summary>${escapeHtml(fn.qualified_name || '未命名函数')} · ${escapeHtml(fn.file_path || '')}</summary>
        <pre class="diff-pre">${escapeHtml(fn.diff_hunk || '暂无 diff_hunk')}</pre>
      </details>`).join('')}</div>`;
    };
    const reportMarkdown = (job) => {
      const result = job.result || {};
      const changedFunctions = result.changed_functions || [];
      const reviewFindings = result.structured_review_findings || [];
      const testCases = enrichTestCasesWithChangeContext(result.structured_test_cases || [], changedFunctions);
      const callStatus = callGraphStatusInfo(job);
      const lines = [
        `# ${job.request.project_name} 代码影响面分析报告`,
        '',
        `- Git：${job.request.git_url}`,
        `- 分支：${job.request.branch}`,
        `- 修改前 Commit：${job.request.before_commit}`,
        `- 修改后 Commit：${job.request.after_commit}`,
        `- 调用链深度：${job.request.call_graph_depth || result.call_graph?.depth || 2}`,
        `- 改动函数数：${changedFunctions.length}`,
        `- 评审发现数：${reviewFindings.length}`,
        `- 测试用例数：${testCases.length}`,
        `- DeusData/codebase-memory-mcp 调用链状态：${callStatus.label}（${callStatus.detail}）`,
        '',
        '## 影响摘要',
        result.impact_summary || '暂无',
        '',
        '## 改动函数',
        ...(changedFunctions.length ? changedFunctions.map((fn) => `- ${fn.qualified_name}（${fn.language}，${fn.change_type || 'modified'}）：${fn.signature}`) : ['- 暂无']),
        '',
        '## 评审发现（按业务功能分组）',
      ];
      groupByBusinessFeature(reviewFindings).forEach((group) => {
        lines.push('', `### ${group.feature}`);
        group.entries.forEach((item) => {
          lines.push(`- 【${impactLevelLabel(itemImpactLevel(item))}】业务小功能：${itemBusinessSubfeature(item)}；${item.finding || ''}`);
        });
      });
      lines.push('', '## 测试标签（按业务功能分组）');
      groupByBusinessFeature(testCases).forEach((group) => {
        lines.push('', `### ${group.feature}`);
        const flows = uniqueItems(group.entries.flatMap((item) => {
          const explicit = arrayFrom(item.affected_business_flows);
          const fallback = bilingualLabel(item.business_subfeature || itemBusinessSubfeature(item), item.business_subfeature_en);
          return explicit.length ? explicit : [fallback];
        }));
        const changedSymbols = uniqueItems(group.entries.flatMap((item) => [...arrayFrom(item.changed_symbols), item.target]));
        const affectedFiles = uniqueItems(group.entries.flatMap((item) => arrayFrom(item.affected_files)));
        lines.push('', `受影响功能点（${flows.length}）：`);
        flows.forEach((flow) => lines.push(`- ${flow}`));
        lines.push('', `回归测试清单（${group.entries.length}）：`);
        group.entries.forEach((item, index) => {
          const id = item.test_id || `TC${String(index + 1).padStart(3, '0')}`;
          const goal = item.verification_goal || arrayFrom(item.covers)[0] || item.name || 'verify impacted behavior';
          lines.push(`- ${id}: ${item.name || itemBusinessSubfeature(item)} -- ${goal}`);
        });
        lines.push('', `变更符号：${(changedSymbols.length ? changedSymbols : ['暂无']).join('、')}`);
        lines.push(`涉及文件：${(affectedFiles.length ? affectedFiles : ['暂无明确文件']).join('、')}`);
      });
      lines.push('', '## Git Diff（默认折叠展示于看板）');
      changedFunctions.forEach((fn) => {
        lines.push('', `### ${fn.qualified_name || '未命名函数'} · ${fn.file_path || ''}`, '```diff', fn.diff_hunk || '暂无 diff_hunk', '```');
      });
      return lines.join('\\n');
    };
    const downloadMarkdownReport = (jobId) => {
      const job = latestJobs.find((candidate) => candidate.id === jobId);
      if (!job || !job.result) return;
      const blob = new Blob([reportMarkdown(job)], { type: 'text/markdown;charset=utf-8' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `${job.request.project_name}-${shortCommit(job.request.after_commit)}-impact-report.md`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(link.href);
    };
    const formatLogTime = (value) => {
      if (!value) return '时间未知';
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return date.toLocaleString('zh-CN', { hour12: false });
    };
    const analysisLogs = (job) => {
      const explicitLogs = (job && job.logs) || [];
      if (explicitLogs.length) {
        return explicitLogs;
      }
      const derived = ((job && job.progress) || []).map((stage) => ({
        time: job.updated_at || job.created_at,
        stage,
        level: stage === 'failed' ? 'error' : 'info',
        message: stageLabels[stage] || stage,
        detail: stage === 'failed' ? (job.error || '') : ''
      }));
      if (job && job.error && !derived.some((entry) => entry.stage === 'failed')) {
        derived.push({
          time: job.updated_at || job.created_at,
          stage: 'failed',
          level: 'error',
          message: '分析失败',
          detail: job.error
        });
      }
      return derived;
    };
    const renderAnalysisLogs = (job) => {
      const logs = analysisLogs(job);
      if (!logs.length) {
        return '<div class="log-list"><p class="muted">暂无分析日志</p></div>';
      }
      return `<div class="log-list">${logs.map((entry, index) => {
        const level = entry.level || (entry.stage === 'failed' ? 'error' : 'info');
        const stage = entry.stage || 'unknown';
        const label = stageLabels[stage] || stage;
        const detail = entry.detail || '';
        return `<details class="log-entry ${escapeHtml(level)}" ${level === 'error' || index === logs.length - 1 ? 'open' : ''}>
          <summary><span>${escapeHtml(label)} · ${escapeHtml(entry.message || label)}</span><span class="muted">${escapeHtml(formatLogTime(entry.time))}</span></summary>
          <div class="log-body">
            <div><strong>阶段：</strong>${escapeHtml(stage)}　<strong>级别：</strong>${escapeHtml(level)}</div>
            ${detail ? `<pre class="log-detail">${escapeHtml(detail)}</pre>` : '<span class="muted">无更多详情</span>'}
          </div>
        </details>`;
      }).join('')}</div>`;
    };
    const renderAnalysisDetail = (jobId) => {
      const job = latestJobs.find((candidate) => candidate.id === jobId);
      const target = document.querySelector('#analysis-detail');
      if (!job) {
        target.innerHTML = '<span class="muted">暂无可查看的完成结果。</span>';
        return;
      }
      if (!job.result) {
        target.classList.remove('muted');
        target.innerHTML = `<div class="report-layout">
          <section class="report-section">
            <div class="report-section-head"><h3>${escapeHtml(job.request.project_name)} 分析日志</h3><span class="status-badge failed">${escapeHtml(job.status)}</span></div>
            <div class="feature-group">
              <p>Git：${escapeHtml(job.request.git_url)}</p>
              <p>分支：${escapeHtml(job.request.branch)}</p>
              <p>Commit：${escapeHtml(job.request.before_commit)} → ${escapeHtml(job.request.after_commit)}</p>
              <p>失败原因：${escapeHtml(job.error || '暂无错误详情')}</p>
            </div>
          </section>
          <section class="report-section"><div class="report-section-head"><h3>分析日志</h3><span class="muted">点击每条日志展开详情</span></div>${renderAnalysisLogs(job)}</section>
        </div>`;
        return;
      }
      const result = job.result;
      const changedFunctionItems = result.changed_functions || [];
      const tokenUsage = result.token_usage || {};
      const reviewFindings = result.structured_review_findings || [];
      const testCases = enrichTestCasesWithChangeContext(result.structured_test_cases || [], changedFunctionItems);
      const maxImpact = [...reviewFindings, ...testCases].map(itemImpactLevel).sort((a, b) => ['critical', 'high', 'medium', 'low'].indexOf(a) - ['critical', 'high', 'medium', 'low'].indexOf(b))[0] || 'low';
      const callStatus = callGraphStatusInfo(job);
      target.classList.remove('muted');
      target.innerHTML = `
        <div class="report-layout">
          <div class="report-hero">
            <div class="report-title-row">
        <div><h3>${escapeHtml(job.request.project_name)} 影响面分析报告</h3><p class="subtitle">${escapeHtml(job.request.branch)} · ${escapeHtml(shortCommit(job.request.before_commit))} → ${escapeHtml(shortCommit(job.request.after_commit))} · ${escapeHtml(job.request.call_graph_depth || result.call_graph?.depth || 2)} 层调用链</p></div>
              <div class="report-actions">${renderImpactBadge(maxImpact)}<button type="button" class="secondary" id="download-markdown-report">下载 Markdown</button></div>
            </div>
            <p class="report-summary">${escapeHtml(result.impact_summary || '暂无影响摘要')}</p>
            <div class="report-stats">
              <div class="report-stat"><span>改动函数</span><strong>${(result.changed_functions || []).length}</strong></div>
              <div class="report-stat"><span>评审发现</span><strong>${reviewFindings.length}</strong></div>
              <div class="report-stat"><span>测试用例</span><strong>${testCases.length}</strong></div>
              <div class="report-stat"><span>调用链状态</span><strong title="${escapeHtml(callStatus.detail)}">${escapeHtml(callStatus.label)}</strong></div>
            </div>
          </div>
          <section class="report-section"><div class="report-section-head"><h3>完整 Commit</h3><span class="muted">详情可复制</span></div><div class="feature-group"><p>Git：${escapeHtml(job.request.git_url)}</p><p>分支：${escapeHtml(job.request.branch)}</p><p>修改前：${escapeHtml(job.request.before_commit)}</p><p>修改后：${escapeHtml(job.request.after_commit)}</p><p>调用链深度：${escapeHtml(job.request.call_graph_depth || result.call_graph?.depth || 2)} 层</p></div></section>
          <section class="report-section"><div class="report-section-head"><h3>影响面结果（按业务功能分组）</h3><span class="muted">影响等级来自模型输出或 severity 推导</span></div>${renderGroupedReviewFindings(reviewFindings)}</section>
          <section class="report-section"><div class="report-section-head"><h3>测试功能（按业务功能分组）</h3><span class="muted">覆盖改动行为与回归风险</span></div>${renderGroupedTestCases(testCases)}</section>
          <section class="report-section"><div class="report-section-head"><h3>Git Diff</h3><span class="muted">默认折叠，展开查看 diff_hunk</span></div>${renderDiffHunks(changedFunctionItems)}</section>
          <section class="report-section"><div class="report-section-head"><h3>改动函数与调用链路</h3><span class="muted">${changedFunctionItems.length} 个函数 · ${escapeHtml(renderCallGraphStatus(job))}</span></div><div class="feature-group"><p class="muted">调用链状态：${escapeHtml(callStatus.detail)}</p>${renderCallGraphTree(result.call_graph, changedFunctionItems)}</div></section>
          <section class="report-section"><div class="report-section-head"><h3>分析日志</h3><span class="muted">点击每条日志展开详情</span></div>${renderAnalysisLogs(job)}</section>
          <section class="report-section"><div class="report-section-head"><h3>Markdown 预览</h3><span class="muted">下载内容预览</span></div><div class="feature-group"><pre class="markdown-preview">${escapeHtml(reportMarkdown(job))}</pre></div></section>
        </div>`;
      document.querySelector('#download-markdown-report').addEventListener('click', () => downloadMarkdownReport(jobId));
    };
    const updateMetrics = (jobs) => {
      document.querySelector('#metric-total').textContent = jobs.length;
      document.querySelector('#metric-completed').textContent = jobs.filter((job) => job.status === 'completed').length;
      document.querySelector('#metric-active').textContent = jobs.filter((job) => ['queued', 'running'].includes(job.status)).length;
    };
    const jobSearchText = (job) => [
      job.id,
      job.status,
      job.error,
      job.request && job.request.project_name,
      job.request && job.request.branch,
      job.request && job.request.git_url,
      job.request && job.request.call_graph_depth,
      job.request && job.request.before_commit,
      job.request && job.request.after_commit,
      job.result && job.result.impact_summary,
      job.result && (job.result.changed_functions || []).map((fn) => fn.qualified_name).join(' '),
      job.result && (job.result.review_findings || []).join(' '),
      job.result && (job.result.test_cases || []).join(' '),
      (job.logs || []).map((entry) => [entry.stage, entry.level, entry.message, entry.detail].filter(Boolean).join(' ')).join(' ')
    ].filter(Boolean).join(' ').toLowerCase();
    const filteredJobs = () => {
      const query = (historySearch.value || '').trim().toLowerCase();
      if (!query) {
        return latestJobs;
      }
      return latestJobs.filter((job) => jobSearchText(job).includes(query));
    };
    const stageLabels = {
      queued: '已排队',
      running: '运行中',
      checkout_repository: '拉取代码',
      index_repository: '构建知识图谱',
      extract_changed_functions: '提取改动函数',
      changed_functions: '分析改动函数',
      two_hop_call_graph: '调用链路',
      trace_call_graph: '追踪调用链',
      prompt_build: '构建提示词',
      ai_request: '请求模型',
      ai_response: '模型已响应',
      completed: '已完成',
      failed: '失败'
    };
    const progressPercent = (job) => {
      if (job.status === 'completed') {
        return 100;
      }
      if (job.status === 'failed') {
        return 100;
      }
      const progress = job.progress || [];
      const milestones = ['queued', 'running', 'checkout_repository', 'index_repository', 'extract_changed_functions', 'changed_functions', 'trace_call_graph', 'two_hop_call_graph', 'prompt_build', 'ai_request', 'ai_response', 'completed'];
      const bestIndex = progress.reduce((best, stage) => Math.max(best, milestones.indexOf(stage)), -1);
      return Math.max(8, Math.min(96, Math.round(((bestIndex + 1) / milestones.length) * 100)));
    };
    const renderProgress = (job) => {
      const percent = progressPercent(job);
      const stages = job.progress || [];
      const latestStage = stages.length ? stages[stages.length - 1] : job.status;
      const stageText = stageLabels[latestStage] || latestStage;
      const title = stages.map((stage) => stageLabels[stage] || stage).join(' / ') || stageText;
      return `<div class="progress-line" title="${escapeHtml(title)}"><div class="progress-meta"><span>阶段进度：${escapeHtml(stageText)}</span><span>${percent}%</span></div><progress value="${percent}" max="100"></progress></div>`;
    };
    const renderAnalysisRows = () => {
      const list = document.querySelector('#analysis-list');
      const jobs = filteredJobs();
      if (latestJobs.length === 0) {
        list.innerHTML = '<p class="muted">暂无分析任务</p>';
        return;
      }
      if (jobs.length === 0) {
        list.innerHTML = '<p class="muted">没有匹配的分析历史</p>';
        return;
      }
      list.innerHTML = jobs
        .map((job) => {
          const summary = job.result ? job.result.impact_summary : '';
          const testCount = job.result ? job.result.test_cases.length : 0;
          const logCount = analysisLogs(job).length;
          const tokenUsage = job.result && job.result.token_usage ? `${job.result.token_usage.prompt_chunks} 分片，最大输出 ${job.result.token_usage.max_output_tokens}` : '';
          const graphStatus = renderCallGraphStatus(job);
          const action = job.result || job.error ? `<button type="button" class="secondary" data-job-id="${job.id}">查看</button>` : '';
          const statusClass = job.status === 'completed' ? 'completed' : job.status === 'failed' ? 'failed' : '';
          const commitTitle = `${job.request.before_commit}..${job.request.after_commit}`;
          return `<article class="analysis-card">
            <div class="analysis-main">
              <div class="analysis-title"><span>${escapeHtml(job.request.project_name)}</span><span class="status-badge ${statusClass}">${escapeHtml(job.status)}</span></div>
              <div class="analysis-meta"><span>${escapeHtml(job.request.branch)}</span><span class="commit-range-compact" title="${escapeHtml(commitTitle)}">${escapeHtml(shortCommit(job.request.before_commit))} → ${escapeHtml(shortCommit(job.request.after_commit))}</span></div>
            </div>
            <div class="progress-cell">${renderProgress(job)}</div>
            <div class="analysis-main">
              <div class="summary-line" title="${escapeHtml(summary || job.error || '')}">${escapeHtml(summary || job.error || '等待分析结果')}</div>
              <div class="analysis-meta"><span>测试用例 ${testCount}</span><span>日志 ${logCount}</span><span>${escapeHtml(graphStatus)}</span><span>${escapeHtml(tokenUsage || 'Token 暂无')}</span></div>
            </div>
            <div class="analysis-actions">${action}</div>
          </article>`;
        })
        .join('');
      list.querySelectorAll('[data-job-id]').forEach((button) => {
        button.addEventListener('click', () => {
          renderAnalysisDetail(button.dataset.jobId);
          showTab('detail-panel');
        });
      });
    };
    const refreshAnalyses = () => fetch('/api/analyses')
      .then((response) => response.json())
      .then((payload) => {
        latestJobs = payload.jobs;
        updateMetrics(payload.jobs);
        renderAnalysisRows();
      });

    document.querySelector('#submit-analysis').addEventListener('click', () => {
      fillProjectNameFromGitUrl(gitUrlInput, projectInput);
      const payload = Object.fromEntries(new FormData(form).entries());
      fetch('/api/analyses', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      }).then(() => {
        showTab('history-panel');
        return refreshAnalyses();
      });
    });

    projectInput.addEventListener('input', () => {
      markProjectNameEdited(projectInput);
      if (!profileProjectInput.value) {
        profileProjectInput.value = projectInput.value;
      }
    });
    gitUrlInput.addEventListener('input', () => {
      const derived = fillProjectNameFromGitUrl(gitUrlInput, projectInput);
      if (derived && !profileProjectInput.value.trim()) {
        profileProjectInput.value = derived;
      }
      if (derived && !profileGitUrlInput.value.trim()) {
        profileGitUrlInput.value = gitUrlInput.value;
      }
    });
    profileProjectInput.addEventListener('input', () => {
      markProjectNameEdited(profileProjectInput);
    });
    profileGitUrlInput.addEventListener('input', () => {
      fillProjectNameFromGitUrl(profileGitUrlInput, profileProjectInput);
    });

    document.querySelector('#load-profile').addEventListener('click', () => {
      fillProjectNameFromGitUrl(profileGitUrlInput, profileProjectInput);
      const project = encodeProject(profileProjectInput.value || projectInput.value);
      if (!project) {
        profileStatus.textContent = '请先填写项目名称';
        return;
      }
      fetch(`/api/projects/${project}/business-context`)
        .then((response) => response.json())
        .then((payload) => {
          profileProjectInput.value = payload.project_name;
          profileContextInput.value = payload.business_context;
          profileStatus.textContent = payload.source_path ? '已读取' : '还没有 business.md';
        });
    });

    document.querySelector('#save-profile').addEventListener('click', () => {
      fillProjectNameFromGitUrl(profileGitUrlInput, profileProjectInput);
      const project = encodeProject(profileProjectInput.value || projectInput.value);
      if (!project) {
        profileStatus.textContent = '请先填写项目名称';
        return;
      }
      fetch(`/api/projects/${project}/business-context`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ business_context: profileContextInput.value })
      })
        .then((response) => response.json())
        .then((payload) => {
          profileProjectInput.value = payload.project_name;
          profileStatus.textContent = '已保存';
        });
    });

    const populateModelConfigForm = (providerId) => {
      const config = modelConfigs.find((item) => item.provider_id === providerId);
      if (!config) {
        return;
      }
      modelConfigForm.querySelector('[name="model"]').value = config.model || '';
      modelConfigForm.querySelector('[name="base_url"]').value = config.base_url || '';
      modelConfigForm.querySelector('[name="api_key"]').value = config.api_key || '';
      modelConfigStatus.textContent = config.api_key ? '已显示已保存 API Key' : '未配置 API Key';
    };
    const refreshModelConfigs = () => Promise.all([
      fetch('/api/providers').then((response) => response.json()),
      fetch('/api/model-configs').then((response) => response.json())
    ])
      .then(([_providersPayload, configPayload]) => {
        modelConfigs = configPayload.configs;
        defaultProviderId = configPayload.default_provider_id || '';
        const select = document.querySelector('#provider-select');
        const configSelect = document.querySelector('#model-provider-select');
        const defaultSelect = document.querySelector('#default-provider-select');
        const table = document.querySelector('#provider-table');
        const selectedProviderId = configSelect.value;
        const options = modelConfigs
          .map((provider) => `<option value="${provider.provider_id}">${provider.provider_name} · ${provider.model}</option>`)
          .join('');
        select.innerHTML = options;
        configSelect.innerHTML = options;
        defaultSelect.innerHTML = options;
        const effectiveDefaultProviderId = defaultProviderId || (modelConfigs[0] && modelConfigs[0].provider_id) || '';
        if (effectiveDefaultProviderId) {
          select.value = effectiveDefaultProviderId;
          defaultSelect.value = effectiveDefaultProviderId;
        }
        if (selectedProviderId && modelConfigs.some((provider) => provider.provider_id === selectedProviderId)) {
          configSelect.value = selectedProviderId;
        } else if (effectiveDefaultProviderId) {
          configSelect.value = effectiveDefaultProviderId;
        }
        table.innerHTML = modelConfigs
          .map((provider) => `<tr><td>${escapeHtml(provider.provider_name)}</td><td>${provider.is_default ? '<span class="status-badge completed">默认</span>' : ''}</td><td>${escapeHtml(provider.model)}</td><td>${escapeHtml(provider.default_model)}</td><td>${escapeHtml(provider.api_key || '')}</td><td>${escapeHtml(provider.model_env)}</td><td>${escapeHtml(provider.api_key_env)}</td><td>${escapeHtml(provider.base_url_env)}</td><td>${escapeHtml(provider.family === 'china' ? '国内' : '海外')}</td></tr>`)
          .join('');
        populateModelConfigForm(configSelect.value);
      });
    document.querySelector('#model-provider-select').addEventListener('change', (event) => populateModelConfigForm(event.target.value));
    document.querySelector('#use-default-model').addEventListener('click', () => {
      const providerId = modelConfigForm.querySelector('[name="provider_id"]').value;
      const config = modelConfigs.find((item) => item.provider_id === providerId);
      if (!config) return;
      modelConfigForm.querySelector('[name="model"]').value = config.default_model || '';
      modelConfigForm.querySelector('[name="base_url"]').value = config.default_base_url || '';
      modelConfigStatus.textContent = '已填入供应商默认模型和 Base URL，保存后生效';
    });
    document.querySelector('#set-default-provider').addEventListener('click', () => {
      const providerId = modelConfigForm.querySelector('[name="default_provider_id"]').value || modelConfigForm.querySelector('[name="provider_id"]').value;
      fetch('/api/model-configs/default', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider_id: providerId })
      })
        .then((response) => response.json().then((payload) => ({ ok: response.ok, payload })))
        .then(({ ok, payload }) => {
          modelConfigStatus.textContent = ok ? '默认分析模型已保存' : `保存失败：${payload.error || '请求无效'}`;
          return ok ? refreshModelConfigs() : undefined;
        });
    });
    const saveModelConfig = ({ refresh = true } = {}) => {
      const providerId = modelConfigForm.querySelector('[name="provider_id"]').value;
      const payload = {
        model: modelConfigForm.querySelector('[name="model"]').value,
        base_url: modelConfigForm.querySelector('[name="base_url"]').value,
      };
      const apiKey = modelConfigForm.querySelector('[name="api_key"]').value;
      if (apiKey) {
        payload.api_key = apiKey;
      }
      return fetch(`/api/model-configs/${encodeURIComponent(providerId)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      })
        .then((response) => response.json())
        .then((payload) => {
          modelConfigStatus.textContent = payload.api_key ? '模型配置已保存，API Key 已显示' : '模型配置已保存';
          return refresh ? refreshModelConfigs() : payload;
        });
    };
    document.querySelector('#save-model-config').addEventListener('click', saveModelConfig);
    document.querySelector('#test-model-config').addEventListener('click', () => {
      const providerId = modelConfigForm.querySelector('[name="provider_id"]').value;
      modelConfigStatus.textContent = '正在保存并测试模型...';
      saveModelConfig({ refresh: false })
        .then(() => fetch(`/api/model-configs/${encodeURIComponent(providerId)}/test`, { method: 'POST' }))
        .then((response) => response.json().then((payload) => ({ ok: response.ok, payload })))
        .then(({ ok, payload }) => {
          modelConfigStatus.textContent = ok && payload.ok ? `测试成功：${payload.model}` : `测试失败：${payload.error || '模型无响应'}`;
        })
        .catch((error) => {
          modelConfigStatus.textContent = `测试失败：${error.message}`;
        });
    });

    const renderReviewStandards = () => fetch('/api/review-standards')
      .then((response) => response.json())
      .then((payload) => {
        const table = document.querySelector('#review-standards-table');
        table.innerHTML = payload.standards
          .map((standard) => {
            const sectionNames = Object.keys(standard.sections).join(', ');
            const languageSpecific = standard.sections['语言专项'] || [];
            return `<tr><td>${escapeHtml(standard.language)}</td><td>${escapeHtml(sectionNames)}</td><td>${escapeHtml(languageSpecific.join(' '))}</td></tr>`;
          })
          .join('');
      });
    document.querySelector('#load-review-standard').addEventListener('click', () => {
      const language = reviewStandardForm.querySelector('[name="language"]').value.trim() || 'lua';
      fetch(`/api/review-standards/${encodeURIComponent(language)}`)
        .then((response) => response.json())
        .then((standard) => {
          reviewStandardForm.querySelector('[name="language"]').value = standard.language;
          reviewStandardForm.querySelector('[name="sections"]').value = JSON.stringify(standard.sections, null, 2);
          reviewStandardStatus.textContent = '已读取';
        });
    });
    document.querySelector('#save-review-standard').addEventListener('click', () => {
      const language = reviewStandardForm.querySelector('[name="language"]').value.trim();
      if (!language) {
        reviewStandardStatus.textContent = '请先填写语言';
        return;
      }
      let sections;
      try {
        sections = JSON.parse(reviewStandardForm.querySelector('[name="sections"]').value);
      } catch (_error) {
        reviewStandardStatus.textContent = 'JSON 格式不正确';
        return;
      }
      fetch(`/api/review-standards/${encodeURIComponent(language)}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sections })
      })
        .then((response) => response.json().then((payload) => ({ ok: response.ok, payload })))
        .then(({ ok, payload }) => {
          reviewStandardStatus.textContent = ok ? '评审规范已保存' : `保存失败：${payload.error || '请求无效'}`;
          if (ok) {
            reviewStandardForm.querySelector('[name="language"]').value = payload.language;
            reviewStandardForm.querySelector('[name="sections"]').value = JSON.stringify(payload.sections, null, 2);
            renderReviewStandards();
          }
        });
    });
    document.querySelector('#refresh-analyses').addEventListener('click', refreshAnalyses);
    document.querySelector('#refresh-history').addEventListener('click', refreshAnalyses);
    historySearch.addEventListener('input', renderAnalysisRows);
    refreshModelConfigs();
    renderReviewStandards();
    refreshAnalyses();
  </script>
</body>
</html>"""


def _analysis_result_to_dict(result: ImpactAnalysisResult) -> dict[str, Any]:
    return {
        "project_name": result.project_name,
        "changed_functions": [asdict(function) for function in result.changed_functions],
        "call_graph": asdict(result.call_graph),
        "impact_summary": result.impact_summary,
        "review_findings": result.review_findings,
        "test_cases": result.test_cases,
        "structured_review_findings": result.structured_review_findings,
        "structured_test_cases": result.structured_test_cases,
        "prompt_chunks": result.prompt_chunks,
        "token_usage": result.token_usage,
    }


def _model_config_catalog(model_config_store: InMemoryModelConfigStore) -> dict[str, Any]:
    configured = {config.provider_id: config for config in model_config_store.list()}
    default_provider_id = model_config_store.default_provider_id()
    return {
        "default_provider_id": default_provider_id,
        "configs": [
            _model_config_to_public_dict(
                configured.get(provider.id, ModelConfig(provider_id=provider.id)),
                provider,
                is_default=provider.id == default_provider_id,
            )
            for provider in provider_catalog()
        ],
    }


def _model_config_to_public_dict(config: ModelConfig, provider, is_default: bool = False) -> dict[str, Any]:
    model = config.model or provider.default_model
    base_url = config.base_url or provider.default_base_url
    return {
        "provider_id": provider.id,
        "provider_name": provider.name,
        "family": provider.family,
        "is_default": is_default,
        "model": model,
        "base_url": base_url,
        "api_key": config.api_key,
        "api_key_configured": bool(config.api_key),
        "model_env": provider.model_env,
        "api_key_env": provider.api_key_env,
        "base_url_env": provider.base_url_env,
        "default_model": provider.default_model,
        "default_base_url": provider.default_base_url,
        "max_input_tokens": provider.max_input_tokens,
        "max_output_tokens": provider.max_output_tokens,
    }


def _apply_model_config_to_analyzer(analyzer: Analyzer | None, config: ModelConfig) -> None:
    if analyzer is None or not hasattr(analyzer, "ai_client"):
        return
    provider = _provider_by_id(config.provider_id)
    if provider is None:
        return
    ai_client = analyzer.ai_client
    if hasattr(ai_client, "models"):
        if config.model:
            ai_client.models[provider.id] = config.model
        else:
            ai_client.models.pop(provider.id, None)
    if hasattr(ai_client, "base_urls"):
        if config.base_url:
            ai_client.base_urls[provider.base_url_env] = config.base_url
        else:
            ai_client.base_urls.pop(provider.base_url_env, None)
    if hasattr(ai_client, "api_keys") and config.api_key:
        ai_client.api_keys[provider.api_key_env] = config.api_key


def _apply_review_standard_to_analyzer(analyzer: Analyzer | None, standard) -> None:
    if analyzer is None or not hasattr(analyzer, "review_standard_store") or analyzer.review_standard_store is None:
        return
    analyzer.review_standard_store.save(standard.language, standard.sections)


def _model_name_for_analyzer(analyzer: Analyzer, provider) -> str:
    ai_client = analyzer.ai_client
    if hasattr(ai_client, "_model_for"):
        return ai_client._model_for(provider)
    if hasattr(ai_client, "models"):
        return ai_client.models.get(provider.id, provider.default_model)
    return provider.default_model


def _test_model_with_timeout(ai_client, provider) -> dict:
    result: dict[str, Any] = {}

    def run_probe() -> None:
        try:
            result["response"] = ai_client.complete(
                "Return JSON exactly as {\"ok\": true, \"message\": \"model is reachable\"}.",
                provider=provider,
                max_output_tokens=64,
            )
        except Exception as error:
            result["error"] = error

    thread = threading.Thread(target=run_probe, daemon=True)
    thread.start()
    thread.join(timeout=MODEL_TEST_TIMEOUT_SECONDS)
    if thread.is_alive():
        raise TimeoutError(f"模型测试超时（{MODEL_TEST_TIMEOUT_SECONDS} 秒）")
    if "error" in result:
        raise result["error"]
    return result.get("response", {})


def _invalid_analysis_fields(payload: dict[str, Any]) -> list[str]:
    invalid = []
    for field in REQUIRED_ANALYSIS_FIELDS:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            invalid.append(field)
    project_name = payload.get("project_name")
    git_url = payload.get("git_url")
    if (
        (not isinstance(project_name, str) or not project_name.strip())
        and (not isinstance(git_url, str) or not _default_project_name_from_git_url(git_url))
    ):
        invalid.append("project_name")
    return invalid


def _analysis_project_name(payload: dict[str, Any]) -> str:
    project_name = payload.get("project_name")
    if isinstance(project_name, str) and project_name.strip():
        return project_name.strip()
    return _default_project_name_from_git_url(str(payload.get("git_url", ""))) or "project"


def _analysis_call_graph_depth(payload: dict[str, Any]) -> int:
    raw_depth = payload.get("call_graph_depth", 2)
    try:
        depth = int(raw_depth)
    except (TypeError, ValueError):
        return 2
    return max(1, min(5, depth))


def _default_project_name_from_git_url(git_url: str) -> str:
    value = git_url.strip()
    if not value:
        return ""
    value = value.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    if "://" not in value and ":" in value and "/" not in value.split(":", 1)[0]:
        value = value.replace(":", "/", 1)
    parts = [part for part in value.split("/") if part]
    if not parts:
        return ""
    selected = parts[-2:] if len(parts) >= 2 else parts[-1:]
    return "-".join(selected)


def _supported_provider_ids() -> list[str]:
    return [provider.id for provider in provider_catalog()]


def _provider_by_id(provider_id: str):
    return next((provider for provider in provider_catalog() if provider.id == provider_id), None)


def _business_context_project_from_path(path: str) -> str | None:
    if not path.startswith("/api/projects/") or not path.endswith("/business-context"):
        return None
    project_name = path.removeprefix("/api/projects/").removesuffix("/business-context").strip("/")
    return unquote(project_name) if project_name else None


def _profile_to_dict(profile: ProjectProfile) -> dict[str, Any]:
    return {
        "project_name": profile.project_name,
        "business_context": profile.business_context,
        "source_path": str(profile.source_path) if profile.source_path else None,
    }


def _analyze_with_progress(analyzer: Analyzer, request: ImpactAnalysisRequest, progress) -> ImpactAnalysisResult:
    parameters = inspect.signature(analyzer.analyze).parameters
    if "progress" in parameters:
        return analyzer.analyze(request, progress)
    return analyzer.analyze(request)


def main() -> None:
    from impact_ai.runtime import create_configured_server

    server = create_configured_server(("127.0.0.1", 8080))
    stopping = False

    def stop_server(_signum, _frame):
        nonlocal stopping
        stopping = True
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop_server)
    signal.signal(signal.SIGTERM, stop_server)

    print("Impact Analysis AI listening on http://127.0.0.1:8080")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        if not stopping:
            raise
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
