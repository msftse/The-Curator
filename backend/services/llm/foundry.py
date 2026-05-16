"""Azure AI Foundry LLM provider.

Lazily builds an ``azure.ai.inference.aio.ChatCompletionsClient`` from
``Settings``. Credential resolution:

* If ``settings.azure_ai_foundry_api_key`` is non-empty → ``AzureKeyCredential``
  (local dev path).
* Otherwise → ``DefaultAzureCredential`` (prod, expects Managed Identity).

The provider performs network I/O only — no Cosmos / Blob / Redis access.
There are no ``delete_item`` / ``delete_blob`` calls (the AST gate at
``backend/tests/unit/test_never_delete_invariant.py`` scans this module).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from backend.core.config import Settings
from backend.core.errors import LLMProviderError
from backend.services.llm.provider import LLMProvider, LLMResult

if TYPE_CHECKING:  # pragma: no cover — only for type-checkers
    from azure.ai.inference.aio import ChatCompletionsClient


class FoundryLLMProvider(LLMProvider):
    """Production aux-model provider — Azure AI Foundry only."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: ChatCompletionsClient | None = None

    def _build_client(self) -> ChatCompletionsClient:
        try:
            from azure.ai.inference.aio import ChatCompletionsClient
            from azure.core.credentials import AzureKeyCredential
        except Exception as exc:  # pragma: no cover — import guard
            raise LLMProviderError(f"azure-ai-inference SDK is not installed: {exc}") from exc

        if not self._settings.foundry_endpoint:
            raise LLMProviderError("FOUNDRY_ENDPOINT is not configured")

        if self._settings.azure_ai_foundry_api_key:
            credential: Any = AzureKeyCredential(self._settings.azure_ai_foundry_api_key)
        else:
            try:
                from azure.identity.aio import DefaultAzureCredential
            except Exception as exc:  # pragma: no cover
                raise LLMProviderError(f"azure-identity is not installed: {exc}") from exc
            credential = DefaultAzureCredential()

        return ChatCompletionsClient(
            endpoint=self._settings.foundry_endpoint,
            credential=credential,
            api_version=self._settings.foundry_api_version,
        )

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_input_tokens: int,
        max_output_tokens: int,
        response_format: Literal["text", "json_object"] = "json_object",
        temperature: float = 0.0,
    ) -> LLMResult:
        # ``max_input_tokens`` is informational — Foundry does not accept it
        # as a request parameter. We truncate client-side via a rough char-based
        # estimate (4 chars ~= 1 token) so a runaway prompt cannot blow the cap.
        char_budget = max_input_tokens * 4
        truncated_user = user
        if len(user) > char_budget:
            truncated_user = user[:char_budget] + "\n[truncated for length]"

        if self._client is None:
            self._client = self._build_client()

        try:
            from azure.ai.inference.models import SystemMessage, UserMessage
        except Exception as exc:  # pragma: no cover
            raise LLMProviderError(f"azure-ai-inference models missing: {exc}") from exc

        # Some deployments reject ``response_format={"type":"json_object"}`` with
        # HTTP 400. Gate the kwarg behind a settings flag; when off, we rely on
        # the system prompt + lenient JSON parsing in the caller.
        kwargs: dict[str, Any] = {
            "messages": [
                SystemMessage(content=system),
                UserMessage(content=truncated_user),
            ],
            "max_tokens": max_output_tokens,
            "temperature": temperature,
            "model": self._settings.foundry_deployment,
        }
        if response_format == "json_object" and self._settings.foundry_supports_json_object:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = await self._client.complete(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise LLMProviderError(f"Foundry completion failed: {exc}") from exc

        try:
            text = resp.choices[0].message.content or ""
            usage = getattr(resp, "usage", None)
            input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            model_id = getattr(resp, "model", None) or self._settings.foundry_deployment
        except Exception as exc:  # noqa: BLE001
            raise LLMProviderError(f"unexpected Foundry response shape: {exc}") from exc

        return LLMResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_id=str(model_id),
        )
