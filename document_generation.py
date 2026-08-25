"""
document_generation.py

Renders the final PLA Word document from a docxtpl template using the
reviewer-confirmed extracted fields plus the deterministically
calculated treaty figures. No business logic lives here -- it only
formats values and fills the template.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from docxtpl import DocxTemplate

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

TEMPLATE_PATHS = {
    "XL": TEMPLATES_DIR / "xl_pla_template.docx",
    "PROPORTIONAL": TEMPLATES_DIR / "proportional_pla_template.docx",
}


def _fmt_money(value: Any, currency: str | None) -> str:
    if value is None or value == "":
        return ""
    try:
        formatted = f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return str(value)
    return f"{currency} {formatted}".strip() if currency else formatted


def render_xl_pla(context: dict[str, Any]) -> BytesIO:
    """context keys expected: treaty_type, name_of_insured, policy_no, claim_no,
    date_of_loss, period_of_insurance, sum_insured, sum_insured_currency,
    particulars_of_loss, description_of_risk_covered, estimated_amount_of_loss,
    estimated_amount_of_loss_currency, deductible,
    estimated_amount_affected_to_xl_cover (already formatted, e.g. layer-wise
    text), brief_description_of_loss.
    """
    tpl = DocxTemplate(TEMPLATE_PATHS["XL"])
    render_context = {
        **context,
        "sum_insured": _fmt_money(context.get("sum_insured"), context.get("sum_insured_currency")),
        "estimated_amount_of_loss": _fmt_money(
            context.get("estimated_amount_of_loss"), context.get("estimated_amount_of_loss_currency")
        ),
        "deductible": _fmt_money(context.get("deductible"), context.get("sum_insured_currency")),
    }
    tpl.render(render_context)
    buffer = BytesIO()
    tpl.save(buffer)
    buffer.seek(0)
    return buffer


def render_proportional_pla(context: dict[str, Any]) -> BytesIO:
    """context keys expected: treaty_uw_year, name_of_insured, policy_no, claim_no,
    date_of_loss, period_of_insurance, sum_insured, sum_insured_currency,
    particulars_of_loss, description_of_risk_covered, estimated_amount_of_loss,
    estimated_amount_of_loss_currency, retained_loss,
    estimated_amount_affected_to_treaty, brief_description_of_loss.
    """
    tpl = DocxTemplate(TEMPLATE_PATHS["PROPORTIONAL"])
    currency = context.get("sum_insured_currency")
    render_context = {
        **context,
        "sum_insured": _fmt_money(context.get("sum_insured"), currency),
        "estimated_amount_of_loss": _fmt_money(
            context.get("estimated_amount_of_loss"), context.get("estimated_amount_of_loss_currency")
        ),
        "retained_loss": _fmt_money(context.get("retained_loss"), currency),
        "estimated_amount_affected_to_treaty": _fmt_money(
            context.get("estimated_amount_affected_to_treaty"), currency
        ),
    }
    tpl.render(render_context)
    buffer = BytesIO()
    tpl.save(buffer)
    buffer.seek(0)
    return buffer


def render_pla(pla_type: str, context: dict[str, Any]) -> BytesIO:
    if pla_type == "XL":
        return render_xl_pla(context)
    if pla_type == "PROPORTIONAL":
        return render_proportional_pla(context)
    raise ValueError(f"Unknown PLA type '{pla_type}'")
