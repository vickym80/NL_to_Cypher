"""Builds a neo4j_graphrag-compatible LLM client from this project's existing settings.

Deliberately does NOT reuse llm_service.llm_factory.get_llm_client(): that factory
returns clients implementing this repo's own LLMClient Protocol
(llm_service/base_client.py), not neo4j_graphrag.llm.LLMInterface. Both read the
same underlying .env values (LLM_PROVIDER, OPENAI_API_KEY, etc.) via
config.settings.get_settings(), so the two stay consistent without being coupled.
"""

from __future__ import annotations

from neo4j_graphrag.llm import AnthropicLLM, LLMInterface, OpenAILLM

from config.settings import Settings, get_settings


def build_llm(settings: Settings | None = None) -> LLMInterface:
    settings = settings or get_settings()

    if settings.llm_provider == "openai":
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")
        return OpenAILLM(
            model_name=settings.openai_model,
            api_key=settings.openai_api_key.get_secret_value(),
        )

    if settings.llm_provider == "claude":
        if not settings.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY is required when LLM_PROVIDER=claude")
        return AnthropicLLM(
            model_name=settings.anthropic_model,
            api_key=settings.anthropic_api_key.get_secret_value(),
        )

    raise ValueError(f"Unsupported LLM_PROVIDER for NL2Cypher: {settings.llm_provider!r}")
