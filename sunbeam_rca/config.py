"""LLM provider configuration.

Wraps LangChain's init_chat_model to select provider via environment variables.
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

logger = logging.getLogger(__name__)

_PROVIDER_DEFAULTS: dict[str, str] = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-sonnet-4-20250514",
    "ollama": "llama3",
}


def get_llm() -> BaseChatModel | None:
    """Return a configured LLM instance, or ``None`` if unavailable.

    Provider and model are selected via the ``LLM_PROVIDER`` and
    ``LLM_MODEL`` environment variables (or ``.env`` file).
    """
    load_dotenv()

    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL") or _PROVIDER_DEFAULTS.get(provider, "gpt-4o-mini")

    kwargs: dict = {"temperature": 0}
    if provider == "ollama":
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        kwargs["base_url"] = base_url

    try:
        llm = init_chat_model(model, model_provider=provider, **kwargs)
        logger.info("LLM initialised: provider=%s model=%s", provider, model)
        return llm
    except Exception:
        logger.warning(
            "Failed to initialise LLM (provider=%s, model=%s). "
            "Falling back to pattern-only analysis.",
            provider,
            model,
            exc_info=True,
        )
        return None
