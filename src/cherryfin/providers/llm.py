from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Protocol

import httpx


class LLMProviderError(RuntimeError):
    """Raised when the model endpoint fails or returns invalid structured output."""


@dataclass(frozen=True, slots=True)
class LLMResult:
    data: dict[str, Any]
    model: str


class LLMProvider(Protocol):
    async def generate_json(self, *, system_prompt: str, user_prompt: str) -> LLMResult: ...


class OpenAICompatibleProvider:
    """Small provider adapter for LM Studio, Ollama, vLLM, SGLang, and hosted APIs.

    The model can propose analysis, but this class exposes no transaction tools.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 90.0,
        max_attempts: int = 2,
    ) -> None:
        if not model.strip():
            raise ValueError("model must not be empty")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max(1, max_attempts)

    async def generate_json(self, *, system_prompt: str, user_prompt: str) -> LLMResult:
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        last_error: Exception | None = None
        for attempt in range(self._max_attempts):
            try:
                async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                    response = await client.post(
                        f"{self._base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()
                    body = response.json()
                content = self._extract_content(body)
                return LLMResult(data=self._parse_json(content), model=self._model)
            except (httpx.HTTPError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep(0.25 * (2**attempt))

        raise LLMProviderError("model endpoint failed or returned invalid JSON") from last_error

    @staticmethod
    def _extract_content(body: dict[str, Any]) -> str:
        content = body["choices"][0]["message"]["content"]
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
            if parts:
                return "".join(parts)
        raise TypeError("unsupported model content format")

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            first_newline = text.find("\n")
            last_fence = text.rfind("```")
            if first_newline >= 0 and last_fence > first_newline:
                text = text[first_newline + 1 : last_fence].strip()
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise
            result = json.loads(text[start : end + 1])
        if not isinstance(result, dict):
            raise TypeError("model output must be a JSON object")
        return result
