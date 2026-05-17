"""Azure AI Foundry LLM provider, powered by Microsoft Agent Framework (MAF).

Calls go to a Foundry **Project** endpoint via ``agent_framework.foundry.
FoundryChatClient``. The chat client is stateless — system+user messages are
sent on every request, no persistent agent resource is created. This fits our
two consumers (classifier + curator) which both run one-shot completions
with no tools or threads.

Why MAF (and not raw ``openai.AsyncAzureOpenAI``)?

* The configured Foundry endpoint speaks the AOAI URL shape
  (``/openai/deployments/.../chat/completions``) but only when reached through
  the **Project**, not the AI Services account root.
* MAF supports structured outputs natively (``response_format=PydanticClass``),
  which both the classifier and curator review pass rely on for typed JSON.
* It puts us on the same surface the curator will use later for agentic flows
  (hosted tools, threads, MCP) without another migration.

Auth resolution is unchanged: ``azure_ai_foundry_api_key`` wins if set
(local dev escape hatch); otherwise ``DefaultAzureCredential`` ('az login' /
Managed Identity). Your principal needs *Cognitive Services User* (or
*Azure AI Developer*) on the AI Services account that owns the project.

This module performs network I/O only — no Cosmos / Blob / Redis access,
no ``delete_item`` / ``delete_blob`` calls. The AST gate at
``backend/tests/unit/test_never_delete_invariant.py`` still scans this file.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from backend.core.config import Settings
from backend.core.errors import LLMProviderError
from backend.services.llm.provider import LLMProvider, LLMResult, ResponseFormat

if TYPE_CHECKING:  # pragma: no cover — only for type-checkers
    from agent_framework.foundry import FoundryChatClient

log = logging.getLogger(__name__)


class FoundryLLMProvider(LLMProvider):
    """MAF-backed LLM provider — Azure AI Foundry Project endpoint."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client: FoundryChatClient | None = None
        # Mutex guarding lazy client construction. The provider can be shared
        # across concurrent callers (one worker handles uploads serially today,
        # but the curator's batch loop fires several reviews back-to-back and
        # could race in future).
        self._lock = asyncio.Lock()

    async def _ensure_client(self) -> FoundryChatClient:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is not None:
                return self._client
            try:
                from agent_framework.foundry import FoundryChatClient
            except Exception as exc:  # pragma: no cover — import guard
                raise LLMProviderError(f"agent-framework-foundry is not installed: {exc}") from exc

            if not self._settings.azure_ai_project_endpoint:
                raise LLMProviderError(
                    "AZURE_AI_PROJECT_ENDPOINT is not configured. "
                    "Set it to the Foundry Project endpoint, e.g. "
                    "https://<acct>.services.ai.azure.com/api/projects/<project>."
                )
            if not self._settings.foundry_deployment:
                raise LLMProviderError("FOUNDRY_DEPLOYMENT is not configured")

            credential = self._build_credential()

            self._client = FoundryChatClient(
                project_endpoint=self._settings.azure_ai_project_endpoint,
                model=self._settings.foundry_deployment,
                credential=credential,
            )
            log.info(
                "foundry.llm.client_built project_endpoint=%s model=%s auth=%s",
                self._settings.azure_ai_project_endpoint,
                self._settings.foundry_deployment,
                "api_key"
                if self._settings.azure_ai_foundry_api_key
                else "default_azure_credential",
            )
            return self._client

    def _build_credential(self) -> Any:
        # Local-dev escape hatch: an API key was provided. MAF's
        # FoundryChatClient accepts an AzureKeyCredential anywhere a credential
        # is expected.
        if self._settings.azure_ai_foundry_api_key:
            try:
                from azure.core.credentials import AzureKeyCredential
            except Exception as exc:  # pragma: no cover
                raise LLMProviderError(f"azure-core not installed: {exc}") from exc
            return AzureKeyCredential(self._settings.azure_ai_foundry_api_key)
        try:
            from azure.identity.aio import DefaultAzureCredential
        except Exception as exc:  # pragma: no cover
            raise LLMProviderError(f"azure-identity not installed: {exc}") from exc
        return DefaultAzureCredential()

    async def complete(
        self,
        *,
        system: str,
        user: str,
        max_input_tokens: int,
        max_output_tokens: int,
        response_format: ResponseFormat = "json_object",
        temperature: float = 0.0,
    ) -> LLMResult:
        # max_input_tokens is informational. We truncate user-content
        # client-side via a rough char budget so a runaway prompt cannot
        # blow past the deployment's context window.
        char_budget = max_input_tokens * 4
        truncated_user = user
        if len(user) > char_budget:
            truncated_user = user[:char_budget] + "\n[truncated for length]"

        client = await self._ensure_client()

        # MAF response_format mapping. Three cases the protocol accepts:
        #   - "text": no structured output requested.
        #   - "json_object": legacy "give me valid JSON" hint. MAF/Foundry
        #     doesn't expose a literal JSON-mode toggle here — the prompt is
        #     authoritative, and the caller already parses leniently.
        #   - <PydanticClass>: server-side structured output. MAF binds the
        #     schema and validates the response.
        options: dict[str, Any] = {
            "temperature": temperature,
            "max_tokens": max_output_tokens,
        }
        if isinstance(response_format, type):
            options["response_format"] = response_format

        try:
            from agent_framework import Message
        except Exception as exc:  # pragma: no cover
            raise LLMProviderError(f"agent_framework Message missing: {exc}") from exc

        messages = [
            Message(role="system", contents=system),
            Message(role="user", contents=truncated_user),
        ]

        log.info(
            "foundry.llm.request project_endpoint=%s model=%s "
            "system_chars=%d user_chars=%d max_tokens=%d temperature=%s "
            "response_format=%s",
            self._settings.azure_ai_project_endpoint,
            self._settings.foundry_deployment,
            len(system),
            len(truncated_user),
            max_output_tokens,
            temperature,
            getattr(response_format, "__name__", str(response_format)),
        )
        log.debug("foundry.llm.system_prompt %s", system)
        log.debug("foundry.llm.user_prompt %s", truncated_user)

        try:
            response = await client.get_response(messages, options=options)
        except Exception as exc:  # noqa: BLE001
            log.exception("foundry.llm.error model=%s", self._settings.foundry_deployment)
            raise LLMProviderError(f"Foundry completion failed: {exc}") from exc

        text = (response.text or "").strip()
        # `UsageDetails` is a TypedDict at runtime — a plain `dict`. `getattr`
        # never sees its keys, so the previous `getattr(usage, "input_token_count")`
        # always returned 0. Read via dict access. (.get returns None when MAF
        # decided not to surface usage at all — coerce to 0 for the LLMResult
        # int contract.)
        usage = getattr(response, "usage_details", None) or {}
        input_tokens = int(usage.get("input_token_count") or 0)
        output_tokens = int(usage.get("output_token_count") or 0)
        # MAF's `ChatResponse.model` is the served model id (not `model_id` —
        # that attribute does not exist). Falls back to our deployment name
        # if the provider didn't echo a model.
        model_id = getattr(response, "model", None) or self._settings.foundry_deployment

        log.info(
            "foundry.llm.response model=%s input_tokens=%d output_tokens=%d "
            "text_chars=%d text_prefix=%r",
            model_id,
            input_tokens,
            output_tokens,
            len(text),
            text[:200],
        )

        return LLMResult(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_id=str(model_id),
        )
