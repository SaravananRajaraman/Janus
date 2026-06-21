"""LLM provider factory.

All model access goes through get_llm().  Never instantiate
ChatOllama or ChatOpenAI anywhere else — that is the invariant
that makes the one-line provider swap possible.

Supported providers
-------------------
  ollama  (default)  Local model via Ollama.  Files stay private, no API cost.
                     Best models for structured output: qwen3.5, llama3.1 (8B).
  openai             Cloud model.  Requires OPENAI_API_KEY env var.
                     Swap target: gpt-4o-mini is cheap and reliable.

Usage
-----
  from src.llm import get_llm
  llm = get_llm()                          # uses rules.yaml defaults
  llm = get_llm(provider="openai")         # explicit override
  chain = llm.with_structured_output(AnalysisResult)
"""
from __future__ import annotations


def get_llm(
    provider: str = "ollama",
    model: str | None = None,
    *,
    temperature: float = 0,
):
    """Return a LangChain chat model for the given provider.

    Args:
        provider:    "ollama" (default) or "openai".
        model:       Model name override.  Falls back to the recommended
                     default for each provider if None.
        temperature: Sampling temperature.  0 = deterministic (recommended
                     for structured output so the schema is reliably filled).

    Returns:
        A LangChain BaseChatModel instance.  Call .with_structured_output()
        on it to get a chain that returns AnalysisResult objects.
    """
    match provider.lower():
        case "ollama":
            from langchain_ollama import ChatOllama  # noqa: PLC0415

            return ChatOllama(
                model=model or "qwen3.5",
                temperature=temperature,
            )
        case "openai":
            from langchain_openai import ChatOpenAI  # noqa: PLC0415

            return ChatOpenAI(
                model=model or "gpt-4o-mini",
                temperature=temperature,
            )
        case _:
            raise ValueError(
                f"Unknown provider '{provider}'.  Supported: ollama, openai."
            )
