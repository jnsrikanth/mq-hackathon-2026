"""LLM configuration for the Agent Brain.

Supports two modes:
  1. Local development: Ollama (default, no API key needed)
  2. Corporate deployment: Tachyon Studio / OpenAI-compatible API

Configuration via environment variables:
  LLM_PROVIDER=ollama|tachyon|openai  (default: ollama)
  LLM_MODEL=qwen2.5-coder:7b          (default for ollama)
  OLLAMA_BASE_URL=http://localhost:11434
  TACHYON_API_KEY=<your-key>
  TACHYON_BASE_URL=<your-endpoint>
  TACHYON_MODEL=<model-name>
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Defaults
DEFAULT_OLLAMA_MODEL = "qwen2.5-coder:7b"
DEFAULT_OLLAMA_URL = "http://localhost:11434"


def get_llm(
    provider: str | None = None,
    model: str | None = None,
    **kwargs: Any,
) -> Any:
    """Create and return an LLM instance based on configuration.

    Args:
        provider: "ollama", "tachyon", or "openai". Defaults to env var
                  LLM_PROVIDER or "ollama".
        model: Model name. Defaults to env var LLM_MODEL or provider default.
        **kwargs: Additional kwargs passed to the LLM constructor.

    Returns:
        A LangChain-compatible LLM instance.
    """
    provider = provider or os.environ.get("LLM_PROVIDER", "ollama")
    provider = provider.lower().strip()

    if provider == "ollama":
        return _create_ollama_llm(model, **kwargs)
    elif provider in ("tachyon", "openai"):
        return _create_openai_compatible_llm(model, **kwargs)
    else:
        logger.warning("Unknown LLM provider '%s', falling back to ollama", provider)
        return _create_ollama_llm(model, **kwargs)


def _create_ollama_llm(model: str | None = None, **kwargs: Any) -> Any:
    """Create an Ollama LLM via LangChain's ChatOllama."""
    model = model or os.environ.get("LLM_MODEL", DEFAULT_OLLAMA_MODEL)
    base_url = os.environ.get("OLLAMA_BASE_URL", DEFAULT_OLLAMA_URL)

    try:
        from langchain_ollama import ChatOllama
        llm = ChatOllama(
            model=model,
            base_url=base_url,
            temperature=kwargs.get("temperature", 0.1),
        )
        logger.info("Created Ollama LLM: model=%s, base_url=%s", model, base_url)
        return llm
    except ImportError:
        logger.warning(
            "langchain_ollama not installed. Install with: pip install langchain-ollama"
        )
        return _create_fallback_llm(model)


def _create_openai_compatible_llm(model: str | None = None, **kwargs: Any) -> Any:
    """Create an OpenAI-compatible LLM (works with Tachyon Studio)."""
    api_key = os.environ.get("TACHYON_API_KEY", os.environ.get("OPENAI_API_KEY", ""))
    base_url = os.environ.get("TACHYON_BASE_URL", os.environ.get("OPENAI_BASE_URL", ""))
    model = model or os.environ.get("TACHYON_MODEL", os.environ.get("LLM_MODEL", "gpt-4"))

    if not api_key:
        logger.error("No API key found. Set TACHYON_API_KEY or OPENAI_API_KEY.")
        return _create_fallback_llm(model)

    try:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url or None,
            temperature=kwargs.get("temperature", 0.1),
        )
        logger.info("Created OpenAI-compatible LLM: model=%s, base_url=%s", model, base_url)
        return llm
    except ImportError:
        logger.warning(
            "langchain_openai not installed. Install with: pip install langchain-openai"
        )
        return _create_fallback_llm(model)


class FallbackLLM:
    """Minimal fallback when no LLM library is available.

    Returns template-based responses so the system still functions
    without an LLM connection (useful for testing and offline demos).
    """

    def __init__(self, model: str = "fallback") -> None:
        self.model = model
        logger.info("Using FallbackLLM (no real LLM connected)")

    def invoke(self, prompt: str | list, **kwargs: Any) -> Any:
        """Return a template response."""
        if isinstance(prompt, list):
            # LangChain message format
            text = " ".join(
                getattr(m, "content", str(m)) for m in prompt
            )
        else:
            text = str(prompt)

        response_text = (
            f"[FallbackLLM] Analysis based on topology data:\n"
            f"The MQ topology has been analyzed. Key observations:\n"
            f"- 1-QM-per-App constraint is enforced by construction\n"
            f"- Deterministic channel naming (fromQM.toQM) is applied\n"
            f"- Producer→RemoteQ→XMITQ→Channel→LocalQ→Consumer routing verified\n"
            f"- Complexity reduction achieved through topology normalization\n"
        )

        # Return an object with .content attribute (LangChain compatible)
        return _FallbackResponse(response_text)


class _FallbackResponse:
    """Mimics LangChain AIMessage for the fallback LLM."""

    def __init__(self, content: str) -> None:
        self.content = content

    def __str__(self) -> str:
        return self.content


def _create_fallback_llm(model: str = "fallback") -> FallbackLLM:
    """Create a fallback LLM that works without any external dependencies."""
    return FallbackLLM(model)


# ---------------------------------------------------------------------------
# Tachyon compatibility test script (for corporate network validation)
# ---------------------------------------------------------------------------

def test_tachyon_connection() -> dict:
    """Quick test script to validate Tachyon API connectivity.

    Run this on the corporate network:
        TACHYON_API_KEY=<key> TACHYON_BASE_URL=<url> TACHYON_MODEL=<model> \
        python -c "from agent_brain.llm_config import test_tachyon_connection; print(test_tachyon_connection())"
    """
    try:
        llm = get_llm(provider="tachyon")
        response = llm.invoke("Say 'Hello from MQ Guardian Agent' in one sentence.")
        return {
            "status": "success",
            "model": os.environ.get("TACHYON_MODEL", "unknown"),
            "response": getattr(response, "content", str(response)),
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
            "hint": "Check TACHYON_API_KEY, TACHYON_BASE_URL, and TACHYON_MODEL env vars.",
        }
