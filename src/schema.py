"""Pydantic schema for the single AI analysis call.

ONE call returns ALL five fields.  Never split into multiple calls —
local Ollama is too slow for that and structured output is the whole point.

The model that works best for structured output via Ollama:
  qwen2.5   (8B recommended)  — strong JSON adherence
  llama3.1  (8B fallback)     — good but occasionally drifts

Tiny / older models (phi, mistral-7b <0.3) often return malformed JSON.
Lock the model before wiring it to the graph.
"""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

VALID_INTENTS: frozenset[str] = frozenset(
    {
        "invoice",
        "receipt",
        "screenshot",
        "contract",
        "report",
        "notes",
        "code",
        "data",
        "image",
        "archive",
        "other",
    }
)


class AnalysisResult(BaseModel):
    """Structured output returned by the analyze node.

    All five fields come from a single .with_structured_output() call.
    The destination is NOT in here — it is resolved deterministically
    from the category via rules.yaml after the LLM returns.
    """

    category: str = Field(
        description=(
            "The file category.  Must exactly match one of the keys in rules.yaml "
            "(e.g. 'Images', 'Documents', 'Code').  Use 'Other' if unsure."
        )
    )
    intent: str = Field(
        description=(
            "The file's purpose.  One of: "
            "invoice, receipt, screenshot, contract, report, notes, "
            "code, data, image, archive, other."
        )
    )
    summary: str = Field(
        description=(
            "One-sentence summary of the file (max 120 chars).  "
            "Empty string '' when no text content is available."
        )
    )
    rename_to: str = Field(
        description=(
            "Suggested filename.  Format: YYYY-MM_descriptive-slug.ext  "
            "(keep the original extension).  "
            "Example: 2024-06_aws-invoice-q2.pdf"
        )
    )
    confidence: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Confidence in the analysis, 0.0–1.0.",
    )

    # ------------------------------------------------------------------
    # Validators — keep bad model output from propagating downstream
    # ------------------------------------------------------------------

    @field_validator("intent", mode="before")
    @classmethod
    def normalise_intent(cls, v: str) -> str:
        cleaned = str(v).strip().lower()
        return cleaned if cleaned in VALID_INTENTS else "other"

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, v: float) -> float:
        try:
            return max(0.0, min(1.0, float(v)))
        except (TypeError, ValueError):
            return 0.5

    @field_validator("summary", mode="before")
    @classmethod
    def truncate_summary(cls, v: str) -> str:
        s = str(v).strip()
        return s[:120] if len(s) > 120 else s
