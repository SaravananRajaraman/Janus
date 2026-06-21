"""Prompt template for the analyze node.

make_chain(llm, categories) returns the full runnable:
    prompt | llm.with_structured_output(AnalysisResult)

Variables injected at invoke() time:
    today            — YYYY-MM  (used for rename_to date prefix)
    categories       — comma-separated list from rules.yaml keys
    filename         — original filename
    extension        — file extension (e.g. .pdf)
    content_preview  — extracted text or the no-content sentinel string
"""
from __future__ import annotations

from typing import Any

from langchain_core.prompts import ChatPromptTemplate

from src.schema import AnalysisResult

# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM = """\
You are a file organisation assistant. Analyse the given file and return \
structured metadata so it can be filed automatically.

Today's date (use as rename prefix when no date is found): {today}

Valid categories — choose exactly one: {categories}

Valid intents — choose exactly one:
  invoice | receipt | screenshot | contract | report | notes | \
code | data | image | archive | other

Rules for rename_to:
  • Format: YYYY-MM_descriptive-slug.ext
  • Keep the ORIGINAL file extension — never change it
  • Extract a date from the filename or content if one is present; \
otherwise use today's date as the prefix
  • Slug: lowercase, hyphens only, max 40 chars, describes the file purpose
  • Good examples:
      2024-06_aws-invoice-q2.pdf
      2025-01_logo-dark.png
      2024-11_meeting-notes.txt
      2025-03_budget-q1.xlsx

Rules for summary:
  • One sentence, max 120 characters, describing what the file contains
  • Use an empty string "" when no content preview is available\
"""

_HUMAN = """\
Filename:  {filename}
Extension: {extension}
Content preview:
{content_preview}\
"""

_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", _SYSTEM),
        ("human", _HUMAN),
    ]
)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_chain(llm: Any, categories: dict) -> Any:
    """Bind the prompt to a structured-output LLM and return the chain.

    Args:
        llm:         A LangChain chat model (from get_llm()).
        categories:  The categories dict from rules.yaml (keys used for
                     the {categories} prompt variable at invoke time).

    Returns:
        A LangChain Runnable: prompt | llm.with_structured_output(AnalysisResult).
        Call chain.invoke({today, categories, filename, extension, content_preview}).
    """
    # Build the structured-output chain once; reuse it for every file.
    return _PROMPT | llm.with_structured_output(AnalysisResult)
