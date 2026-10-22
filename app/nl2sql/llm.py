from __future__ import annotations

import json
from typing import Literal, Protocol

from .models import QueryAnalysis, SQLDraft
from .prompts import SYSTEM_PROMPT, generation_input, repair_input

SQL_DRAFT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sql": {"type": "string"},
        "parameters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string"},
                    "value": {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "integer"},
                            {"type": "number"},
                            {"type": "boolean"},
                            {"type": "null"},
                        ]
                    },
                },
                "required": ["name", "value"],
            },
        },
        "explanation_fa": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "tables_used": {"type": "array", "items": {"type": "string"}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "needs_clarification": {"type": "boolean"},
        "clarification_question": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    },
    "required": [
        "sql",
        "parameters",
        "explanation_fa",
        "confidence",
        "tables_used",
        "assumptions",
        "needs_clarification",
        "clarification_question",
    ],
}


class SQLGenerator(Protocol):
    model_name: str
    provider_name: str

    async def generate(self, question: str, analysis: QueryAnalysis, context: str) -> SQLDraft: ...

    async def repair(
        self,
        question: str,
        analysis: QueryAnalysis,
        context: str,
        previous: SQLDraft,
        error: str,
    ) -> SQLDraft: ...


class OpenAISQLGenerator:
    def __init__(
        self,
        api_key: str,
        model_name: str,
        base_url: str = "",
        provider_name: Literal["openai", "gapgpt"] = "openai",
        reasoning_effort: str = "low",
        timeout_seconds: float = 45.0,
    ):
        if not api_key:
            raise ValueError(f"API key is required for {provider_name} SQL generation")
        if not model_name:
            raise ValueError(f"Model name is required for {provider_name} SQL generation")
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.provider_name = provider_name
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise RuntimeError("openai package is required for SQL generation") from exc
            client_kwargs = {
                "api_key": self.api_key,
                "timeout": self.timeout_seconds,
                "max_retries": 1,
            }
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            self._client = AsyncOpenAI(**client_kwargs)
        return self._client

    async def generate(self, question: str, analysis: QueryAnalysis, context: str) -> SQLDraft:
        del analysis
        return await self._request(generation_input(question, context))

    async def repair(
        self,
        question: str,
        analysis: QueryAnalysis,
        context: str,
        previous: SQLDraft,
        error: str,
    ) -> SQLDraft:
        del analysis
        return await self._request(
            repair_input(question, context, previous.sql, previous.parameter_dict(), error)
        )

    async def _request(self, input_text: str) -> SQLDraft:
        instructions = SYSTEM_PROMPT
        if self.provider_name == "gapgpt":
            schema_text = json.dumps(
                SQL_DRAFT_SCHEMA,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            instructions = (
                f"{SYSTEM_PROMPT}\n"
                "Return exactly one JSON object matching this JSON Schema. "
                "Do not use key=value lines and do not wrap the object in Markdown:\n"
                f"{schema_text}"
            )
        kwargs = {
            "model": self.model_name,
            "instructions": instructions,
            "input": input_text,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "nl2sql_draft",
                    "strict": True,
                    "schema": SQL_DRAFT_SCHEMA,
                }
            },
        }
        # GapGPT's documented Responses API example does not advertise the
        # OpenAI-specific reasoning option. Keep its request provider-neutral.
        if self.provider_name == "openai" and self.reasoning_effort != "none":
            kwargs["reasoning"] = {"effort": self.reasoning_effort}
        response = await self._get_client().responses.create(**kwargs)
        output_text = getattr(response, "output_text", None)
        if not output_text:
            raise RuntimeError("LLM returned no structured SQL draft")
        try:
            return self._parse_draft(output_text)
        except (json.JSONDecodeError, ValueError) as exc:
            if self.provider_name == "gapgpt":
                return await self._repair_response_format(output_text, instructions, kwargs["text"])
            raise RuntimeError("LLM structured output could not be validated") from exc

    @staticmethod
    def _parse_draft(output_text: str) -> SQLDraft:
        candidate = output_text.strip()
        if candidate.startswith("```") and candidate.endswith("```"):
            lines = candidate.splitlines()
            candidate = "\n".join(lines[1:-1]).strip()
        return SQLDraft.model_validate(json.loads(candidate))

    async def _repair_response_format(
        self,
        invalid_output: str,
        instructions: str,
        text_format: dict[str, object],
    ) -> SQLDraft:
        response = await self._get_client().responses.create(
            model=self.model_name,
            instructions=instructions,
            input=(
                "Convert the following candidate into exactly one valid JSON object "
                "matching the required schema. Preserve its SQL or clarification meaning. "
                "Fill required fields with safe empty/default values when absent. "
                "Return JSON only.\n\nCANDIDATE RESPONSE:\n"
                f"{invalid_output[:12_000]}"
            ),
            text=text_format,
        )
        output_text = getattr(response, "output_text", None)
        if not output_text:
            raise RuntimeError("LLM returned no structured SQL draft")
        try:
            return self._parse_draft(output_text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("LLM structured output could not be validated") from exc


class OpenAICompatibleChatSQLGenerator:
    """Structured SQL generation over an OpenAI-compatible Chat Completions API."""

    def __init__(
        self,
        api_key: str,
        model_name: str,
        base_url: str,
        provider_name: Literal["deepseek", "qwen", "openai_compatible"],
        thinking_enabled: bool = False,
        reasoning_effort: str = "low",
        timeout_seconds: float = 45.0,
    ):
        if not api_key:
            raise ValueError(f"API key is required for {provider_name} SQL generation")
        if not model_name:
            raise ValueError(f"Model name is required for {provider_name} SQL generation")
        if not base_url:
            raise ValueError(f"Base URL is required for {provider_name} SQL generation")
        self.api_key = api_key
        self.model_name = model_name
        self.base_url = base_url.rstrip("/")
        self.provider_name = provider_name
        self.thinking_enabled = thinking_enabled
        self.reasoning_effort = reasoning_effort
        self.timeout_seconds = timeout_seconds
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise RuntimeError(
                    "openai package is required for OpenAI-compatible SQL generation"
                ) from exc
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                max_retries=1,
            )
        return self._client

    async def generate(self, question: str, analysis: QueryAnalysis, context: str) -> SQLDraft:
        del analysis
        return await self._request(generation_input(question, context))

    async def repair(
        self,
        question: str,
        analysis: QueryAnalysis,
        context: str,
        previous: SQLDraft,
        error: str,
    ) -> SQLDraft:
        del analysis
        return await self._request(
            repair_input(question, context, previous.sql, previous.parameter_dict(), error)
        )

    async def _request(self, input_text: str) -> SQLDraft:
        schema_text = json.dumps(SQL_DRAFT_SCHEMA, ensure_ascii=False, separators=(",", ":"))
        messages = [
            {
                "role": "system",
                "content": (
                    f"{SYSTEM_PROMPT}\n"
                    "Return exactly one JSON object matching this JSON Schema. "
                    "Do not wrap it in Markdown:\n"
                    f"{schema_text}"
                ),
            },
            {"role": "user", "content": input_text},
        ]
        kwargs: dict[str, object] = {
            "model": self.model_name,
            "messages": messages,
            "response_format": {"type": "json_object"},
        }
        extra_body: dict[str, object] = {}
        if self.provider_name == "deepseek":
            extra_body["thinking"] = {
                "type": "enabled" if self.thinking_enabled else "disabled"
            }
            if self.thinking_enabled:
                extra_body["reasoning_effort"] = (
                    "max" if self.reasoning_effort == "max" else "high"
                )
        elif self.provider_name == "qwen":
            extra_body["enable_thinking"] = self.thinking_enabled
        if extra_body:
            kwargs["extra_body"] = extra_body

        response = await self._get_client().chat.completions.create(**kwargs)
        choices = getattr(response, "choices", None) or []
        content = (
            getattr(getattr(choices[0], "message", None), "content", None)
            if choices
            else None
        )
        if not content:
            raise RuntimeError("LLM returned no structured SQL draft")
        try:
            return SQLDraft.model_validate(json.loads(content))
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("LLM structured output could not be validated") from exc


def build_sql_generator(
    provider: str,
    api_key: str,
    model_name: str,
    base_url: str,
    reasoning_effort: str = "low",
    thinking_enabled: bool = False,
    timeout_seconds: float = 45.0,
) -> SQLGenerator | None:
    if not api_key:
        return None
    if provider in {"openai", "gapgpt"}:
        return OpenAISQLGenerator(
            api_key=api_key,
            model_name=model_name,
            base_url=base_url,
            provider_name=provider,
            reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds,
        )
    if provider in {"deepseek", "qwen", "openai_compatible"}:
        return OpenAICompatibleChatSQLGenerator(
            api_key=api_key,
            model_name=model_name,
            base_url=base_url,
            provider_name=provider,
            thinking_enabled=thinking_enabled,
            reasoning_effort=reasoning_effort,
            timeout_seconds=timeout_seconds,
        )
    raise ValueError(f"Unsupported LLM provider: {provider}")


class StaticSQLGenerator:
    """Dependency-injection test double; never selected by production settings."""

    def __init__(self, drafts: list[SQLDraft]):
        self.model_name = "static-test-generator"
        self.provider_name = "static"
        self._drafts = list(drafts)

    async def generate(self, question: str, analysis: QueryAnalysis, context: str) -> SQLDraft:
        del question, analysis, context
        return self._drafts.pop(0)

    async def repair(
        self,
        question: str,
        analysis: QueryAnalysis,
        context: str,
        previous: SQLDraft,
        error: str,
    ) -> SQLDraft:
        del question, analysis, context, previous, error
        return self._drafts.pop(0)
