"""
extraction.py

Turns raw claim-document text into structured PLA fields using Gemini,
validated against Pydantic schemas. This module never performs treaty
math -- it only extracts what is explicitly stated in the source text.
"""

from __future__ import annotations

import json
import os
from typing import Literal, Optional

from google import genai
from pydantic import BaseModel, Field, ValidationError

MODEL_NAME = "gemini-3.1-flash-lite"

PLAType = Literal["XL", "PROPORTIONAL"]


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------

class BaseClaimFields(BaseModel):
    """Fields common to both PLA templates (items 2-10 and 13)."""

    name_of_insured: Optional[str] = None
    policy_no: Optional[str] = None
    claim_no: Optional[str] = None
    date_of_loss: Optional[str] = None
    period_of_insurance: Optional[str] = None
    sum_insured: Optional[float] = None
    sum_insured_currency: Optional[str] = None
    particulars_of_loss: Optional[str] = None
    description_of_risk_covered: Optional[str] = None
    estimated_amount_of_loss: Optional[float] = None
    estimated_amount_of_loss_currency: Optional[str] = None
    brief_description_of_loss: Optional[str] = None


class XLClaimFields(BaseClaimFields):
    """Non-proportional (XL) PLA fields."""

    treaty_type: Optional[str] = None
    deductible: Optional[float] = None
    estimated_amount_affected_to_xl_cover: Optional[str] = Field(
        default=None,
        description="Layer-wise breakdown as stated in the source document, if any.",
    )


class ProportionalClaimFields(BaseClaimFields):
    """Proportional PLA fields."""

    treaty_uw_year: Optional[str] = None
    retained_loss: Optional[float] = None
    estimated_amount_affected_to_treaty: Optional[float] = None


class ExtractionResult(BaseModel):
    """Wraps the parsed fields plus metadata about how extraction went."""

    fields: BaseClaimFields
    raw_output: str
    used_fallback_raw: bool = False
    error: Optional[str] = None


# --------------------------------------------------------------------------
# Prompting
# --------------------------------------------------------------------------

_FIELD_LISTS = {
    "XL": list(XLClaimFields.model_fields.keys()),
    "PROPORTIONAL": list(ProportionalClaimFields.model_fields.keys()),
}

_SYSTEM_INSTRUCTION_TEMPLATE = """You are extracting structured data for a reinsurance \
Preliminary Loss Advice (PLA) from insurer-submitted claim documents.

Return ONLY a single JSON object with exactly these keys, and no others:
{field_list}

Rules (follow strictly):
- Use null for any field not EXPLICITLY stated in the source text.
- NEVER infer, estimate, guess, or calculate a value. If it is not written in the
  document, the value is null.
- For monetary fields, extract the numeric amount as a plain number (no currency
  symbols, no thousands separators) into the "_amount" style field, and put the
  currency code or symbol as written (e.g. "USD", "KES", "$") into the matching
  "_currency" field if one exists.
- Dates should be copied as written in the source document; do not reformat them.
- Return raw JSON only -- no markdown code fences, no commentary, no preamble.
"""


def _build_system_instruction(pla_type: PLAType) -> str:
    fields = _FIELD_LISTS[pla_type]
    return _SYSTEM_INSTRUCTION_TEMPLATE.format(field_list=", ".join(fields))


def _schema_for(pla_type: PLAType) -> type[BaseClaimFields]:
    return XLClaimFields if pla_type == "XL" else ProportionalClaimFields


def _strip_code_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]
    return text.strip()


# --------------------------------------------------------------------------
# Gemini client
# --------------------------------------------------------------------------

import streamlit as st  # add this import at the top of extraction.py

def _get_client() -> genai.Client:
    api_key = None
    try:
        api_key = st.secrets.get("GEMINI_API_KEY")
    except Exception:
        pass  # no secrets.toml locally — fine, fall through to .env/os.environ
    api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No Gemini API key found. Set GEMINI_API_KEY in Streamlit Cloud's "
            "app secrets (or a local .env for local runs)."
        )
    return genai.Client(api_key=api_key)


def _call_gemini(client: genai.Client, system_instruction: str, source_text: str) -> str:
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=source_text,
        config={
            "system_instruction": system_instruction,
            "response_mime_type": "application/json",
            "temperature": 0,
        },
    )
    return (response.text or "").strip()


def extract_fields(source_text: str, pla_type: PLAType) -> ExtractionResult:
    """Send document text to Gemini and validate the JSON response.

    On a schema mismatch, retries once with a stricter reminder. If it
    still fails, returns the raw model output so the reviewer can see
    and fix it manually rather than the app crashing.
    """
    schema_cls = _schema_for(pla_type)
    system_instruction = _build_system_instruction(pla_type)

    if not source_text.strip():
        return ExtractionResult(
            fields=schema_cls(),
            raw_output="",
            error="No text was extracted from the uploaded document(s) to send for extraction.",
        )

    try:
        client = _get_client()
    except RuntimeError as exc:
        return ExtractionResult(fields=schema_cls(), raw_output="", error=str(exc))

    last_raw = ""
    for attempt in range(2):
        try:
            prompt = source_text
            if attempt == 1:
                prompt = (
                    source_text
                    + "\n\nREMINDER: respond with ONLY valid JSON matching the required "
                    "keys exactly. No markdown, no extra keys, no commentary."
                )
            raw = _call_gemini(client, system_instruction, prompt)
            last_raw = raw
            cleaned = _strip_code_fences(raw)
            data = json.loads(cleaned)
            fields = schema_cls.model_validate(data)
            return ExtractionResult(fields=fields, raw_output=raw)
        except (json.JSONDecodeError, ValidationError) as exc:
            if attempt == 0:
                continue
            return ExtractionResult(
                fields=schema_cls(),
                raw_output=last_raw,
                used_fallback_raw=True,
                error=f"Gemini's output did not match the expected schema after retrying: {exc}",
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to UI, e.g. API/network errors
            return ExtractionResult(
                fields=schema_cls(),
                raw_output=last_raw,
                error=f"Gemini extraction failed: {exc}",
            )

    # Unreachable, but keeps type-checkers happy.
    return ExtractionResult(fields=schema_cls(), raw_output=last_raw, error="Unknown extraction failure.")
