import json
import os
import re
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple, Union
from urllib import request
from urllib.error import HTTPError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from impact_ai.ai_providers import AIProvider


class AIClientError(RuntimeError):
    pass


class OpenAICompatibleClient:
    def __init__(
        self,
        api_keys: Optional[Dict[str, str]] = None,
        base_urls: Optional[Dict[str, str]] = None,
        models: Optional[Dict[str, str]] = None,
    ):
        self.api_keys = api_keys or {}
        self.base_urls = base_urls or {}
        self.models = models or {}

    def complete(self, prompt: str, provider: AIProvider, max_output_tokens: int) -> dict:
        api_key = self.api_keys.get(provider.api_key_env) or os.environ.get(provider.api_key_env)
        if not api_key:
            raise AIClientError(f"Missing API key env: {provider.api_key_env}")

        base_url = (
            self.base_urls.get(provider.base_url_env)
            or os.environ.get(provider.base_url_env)
            or provider.default_base_url
        ).rstrip("/")
        if provider.api_format == "anthropic_messages":
            return self._complete_anthropic(prompt, provider, api_key, base_url, max_output_tokens)
        if provider.api_format == "gemini_generate_content":
            return self._complete_gemini(prompt, provider, api_key, base_url, max_output_tokens)
        return self._complete_openai_compatible(prompt, provider, api_key, base_url, max_output_tokens)

    def _complete_openai_compatible(
        self,
        prompt: str,
        provider: AIProvider,
        api_key: str,
        base_url: str,
        max_output_tokens: int,
    ) -> dict:
        payload = {
            "model": self._model_for(provider),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
            "max_tokens": max_output_tokens,
        }
        if provider.supports_response_format:
            payload["response_format"] = {"type": "json_object"}
        http_request = request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        response_payload = _post_json(http_request)

        content = _extract_content(response_payload)
        return _parse_json_object_content(content)

    def _complete_anthropic(
        self,
        prompt: str,
        provider: AIProvider,
        api_key: str,
        base_url: str,
        max_output_tokens: int,
    ) -> dict:
        payload = {
            "model": self._model_for(provider),
            "max_tokens": max_output_tokens,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}],
        }
        http_request = request.Request(
            f"{_anthropic_base_url(base_url)}/messages",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        response_payload = _post_json(http_request)
        return _parse_json_object_content(_extract_anthropic_content(response_payload))

    def _complete_gemini(
        self,
        prompt: str,
        provider: AIProvider,
        api_key: str,
        base_url: str,
        max_output_tokens: int,
    ) -> dict:
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": max_output_tokens,
                "responseMimeType": "application/json",
            },
        }
        model = quote(self._model_for(provider), safe="")
        http_request = request.Request(
            f"{base_url}/models/{model}:generateContent?key={quote(api_key, safe='')}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        response_payload = _post_json(http_request)
        return _parse_json_object_content(_extract_gemini_content(response_payload))

    def _model_for(self, provider: AIProvider) -> str:
        return self.models.get(provider.id) or os.environ.get(provider.model_env) or provider.default_model


def _anthropic_base_url(base_url: str) -> str:
    return base_url if base_url.rstrip("/").endswith("/v1") else f"{base_url.rstrip('/')}/v1"


def _post_json(http_request: request.Request) -> Dict[str, Any]:
    try:
        with request.urlopen(http_request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise AIClientError(
            f"AI provider HTTP {error.code} for {_safe_request_url(http_request.full_url)}: {_extract_error_message(body)}"
        ) from error


def _safe_request_url(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode(
        [
            (key, "<redacted>" if key.lower() in {"key", "api_key", "apikey", "token"} else value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def _extract_error_message(body: str) -> str:
    if not body:
        return "<empty response body>"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    if isinstance(error, str):
        return error
    return json.dumps(payload, ensure_ascii=False)


def _parse_json_object_content(content: Union[str, Dict]) -> dict:
    if isinstance(content, dict):
        return content

    content = _normalize_json_content(content)
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as error:
        parsed = _last_json_object_in_text(content)
        if parsed is None:
            raise AIClientError("AI response content was not valid JSON.") from error

    if not isinstance(parsed, dict):
        raise AIClientError("AI response JSON must be an object.")
    return parsed


def _last_json_object_in_text(content: str) -> Optional[Dict]:
    decoder = json.JSONDecoder()
    parsed_object = None
    for index, character in enumerate(content):
        if character != "{":
            continue
        try:
            parsed, _end = decoder.raw_decode(content[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            parsed_object = parsed
    return parsed_object


def _normalize_json_content(content: str) -> str:
    stripped = content.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    object_match = re.search(r"(\{.*\})", stripped, flags=re.DOTALL)
    if object_match:
        return object_match.group(1).strip()
    return stripped


def _extract_content(response_payload: Dict[str, Any]) -> Union[str, Dict]:
    try:
        return response_payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise AIClientError("AI response did not include choices[0].message.content.") from error


def _extract_anthropic_content(response_payload: Dict[str, Any]) -> Union[str, Dict]:
    try:
        block = response_payload["content"][0]
        if isinstance(block, dict):
            return block["text"]
        return block
    except (KeyError, IndexError, TypeError) as error:
        raise AIClientError("Anthropic response did not include content[0].text.") from error


def _extract_gemini_content(response_payload: Dict[str, Any]) -> Union[str, Dict]:
    try:
        return response_payload["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as error:
        raise AIClientError("Gemini response did not include candidates[0].content.parts[0].text.") from error
