import json
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from impact_ai.ai_client import OpenAICompatibleClient
from impact_ai.ai_providers import provider_catalog


class CapturingChatHandler(BaseHTTPRequestHandler):
    captured = {}
    content_override = None
    error_status = None
    error_payload = None

    def do_POST(self):
        length = int(self.headers["Content-Length"])
        CapturingChatHandler.captured = {
            "path": self.path,
            "authorization": self.headers.get("Authorization"),
            "x_api_key": self.headers.get("x-api-key"),
            "anthropic_version": self.headers.get("anthropic-version"),
            "body": json.loads(self.rfile.read(length).decode("utf-8")),
        }
        if CapturingChatHandler.error_status:
            body = json.dumps(CapturingChatHandler.error_payload or {}).encode("utf-8")
            self.send_response(CapturingChatHandler.error_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        response = self.response_for_path()
        body = json.dumps(response).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def response_for_path(self):
        content = CapturingChatHandler.content_override or json.dumps(
            {
                "impact_summary": "Refund API is impacted.",
                "review_findings": ["Audit failures need handling."],
                "test_cases": ["Cover refund audit failure."],
            }
        )
        if self.path.endswith("/messages"):
            return {"content": [{"type": "text", "text": content}]}
        if ":generateContent" in self.path:
            return {"candidates": [{"content": {"parts": [{"text": content}]}}]}
        return {"choices": [{"message": {"content": content}}]}

    def log_message(self, format, *args):
        return


class OpenAICompatibleClientTests(unittest.TestCase):
    def tearDown(self):
        CapturingChatHandler.content_override = None
        CapturingChatHandler.error_status = None
        CapturingChatHandler.error_payload = None

    def test_complete_posts_chat_request_and_parses_json_content(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), CapturingChatHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = replace(
                next(provider for provider in provider_catalog() if provider.id == "deepseek"),
                default_base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
                api_key_env="TEST_DEEPSEEK_API_KEY",
            )
            client = OpenAICompatibleClient(api_keys={"TEST_DEEPSEEK_API_KEY": "secret-token"})

            result = client.complete("Analyze this diff.", provider=provider, max_output_tokens=256)

            self.assertEqual(result["impact_summary"], "Refund API is impacted.")
            self.assertEqual(CapturingChatHandler.captured["path"], "/v1/chat/completions")
            self.assertEqual(CapturingChatHandler.captured["authorization"], "Bearer secret-token")
            self.assertEqual(CapturingChatHandler.captured["body"]["model"], provider.default_model)
            self.assertEqual(CapturingChatHandler.captured["body"]["max_tokens"], 256)
            self.assertEqual(CapturingChatHandler.captured["body"]["messages"][0]["content"], "Analyze this diff.")
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_complete_parses_json_wrapped_in_markdown_fence(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), CapturingChatHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            CapturingChatHandler.content_override = (
                "```json\n"
                "{\"impact_summary\":\"Refund API is impacted.\",\"review_findings\":[],\"test_cases\":[]}"
                "\n```"
            )
            provider = replace(
                next(provider for provider in provider_catalog() if provider.id == "deepseek"),
                default_base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
                api_key_env="TEST_DEEPSEEK_API_KEY",
            )
            client = OpenAICompatibleClient(api_keys={"TEST_DEEPSEEK_API_KEY": "secret-token"})

            result = client.complete("Analyze this diff.", provider=provider, max_output_tokens=256)

            self.assertEqual(result["impact_summary"], "Refund API is impacted.")
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_complete_uses_configured_model_override(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), CapturingChatHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = replace(
                next(provider for provider in provider_catalog() if provider.id == "deepseek"),
                default_base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
                api_key_env="TEST_DEEPSEEK_API_KEY",
            )
            client = OpenAICompatibleClient(
                api_keys={"TEST_DEEPSEEK_API_KEY": "secret-token"},
                models={"deepseek": "deepseek-reasoner"},
            )

            client.complete("Analyze this diff.", provider=provider, max_output_tokens=256)

            self.assertEqual(CapturingChatHandler.captured["body"]["model"], "deepseek-reasoner")
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_complete_includes_provider_error_response_body(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), CapturingChatHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            CapturingChatHandler.error_status = 400
            CapturingChatHandler.error_payload = {
                "error": {
                    "message": "max context length exceeded",
                    "type": "invalid_request_error",
                }
            }
            provider = replace(
                next(provider for provider in provider_catalog() if provider.id == "deepseek"),
                default_base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
                api_key_env="TEST_DEEPSEEK_API_KEY",
            )
            client = OpenAICompatibleClient(api_keys={"TEST_DEEPSEEK_API_KEY": "secret-token"})

            with self.assertRaisesRegex(Exception, "max context length exceeded"):
                client.complete("Analyze this diff.", provider=provider, max_output_tokens=256)
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_complete_posts_anthropic_messages_request_and_parses_json_content(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), CapturingChatHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = replace(
                next(provider for provider in provider_catalog() if provider.id == "anthropic"),
                default_base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
                api_key_env="TEST_ANTHROPIC_API_KEY",
            )
            client = OpenAICompatibleClient(api_keys={"TEST_ANTHROPIC_API_KEY": "secret-token"})

            result = client.complete("Analyze this diff.", provider=provider, max_output_tokens=256)

            self.assertEqual(result["impact_summary"], "Refund API is impacted.")
            self.assertEqual(CapturingChatHandler.captured["path"], "/v1/messages")
            self.assertIsNone(CapturingChatHandler.captured["authorization"])
            self.assertEqual(CapturingChatHandler.captured["x_api_key"], "secret-token")
            self.assertEqual(CapturingChatHandler.captured["anthropic_version"], "2023-06-01")
            self.assertEqual(CapturingChatHandler.captured["body"]["model"], provider.default_model)
            self.assertEqual(CapturingChatHandler.captured["body"]["max_tokens"], 256)
            self.assertEqual(CapturingChatHandler.captured["body"]["messages"][0]["content"], "Analyze this diff.")
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

    def test_complete_posts_gemini_generate_content_request_and_parses_json_content(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), CapturingChatHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            provider = replace(
                next(provider for provider in provider_catalog() if provider.id == "gemini"),
                default_base_url=f"http://127.0.0.1:{server.server_address[1]}/v1beta",
                api_key_env="TEST_GEMINI_API_KEY",
            )
            client = OpenAICompatibleClient(api_keys={"TEST_GEMINI_API_KEY": "secret-token"})

            result = client.complete("Analyze this diff.", provider=provider, max_output_tokens=256)

            self.assertEqual(result["impact_summary"], "Refund API is impacted.")
            self.assertEqual(
                CapturingChatHandler.captured["path"],
                f"/v1beta/models/{provider.default_model}:generateContent?key=secret-token",
            )
            self.assertIsNone(CapturingChatHandler.captured["authorization"])
            self.assertEqual(
                CapturingChatHandler.captured["body"]["contents"][0]["parts"][0]["text"],
                "Analyze this diff.",
            )
            self.assertEqual(CapturingChatHandler.captured["body"]["generationConfig"]["maxOutputTokens"], 256)
            self.assertEqual(CapturingChatHandler.captured["body"]["generationConfig"]["responseMimeType"], "application/json")
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
