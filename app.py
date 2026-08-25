"""
app.py

PLA Assistant -- Streamlit UI wiring only. Business logic (extraction,
treaty math, document generation) lives in the other modules; this
file is purely responsible for the multi-step wizard and session state.
"""

from __future__ import annotations

from typing import Any

from dotenv import load_dotenv
import streamlit as st

load_dotenv()

from calculators import (
    TreatyCalculationError,
    XLLayerInput,
    get_calculator,
)
from document_generation import render_pla
from document_parsing import combine_texts, parse_documents
from extraction import BaseClaimFields, ProportionalClaimFields, XLClaimFields, extract_fields

STEP_LABELS = ["Upload & Select Type", "Extraction & Review", "Treaty Details", "Generate & Download"]

st.set_page_config(page_title="PLA Assistant", page_icon=None, layout="wide")


# --------------------------------------------------------------------------
# Styling
# --------------------------------------------------------------------------

def inject_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --navy: #1B2A4A;
            --charcoal: #333333;
            --accent: #B08D57;
        }
        .stApp { background-color: #FFFFFF; }
        h1, h2, h3 { color: var(--navy); font-weight: 600; letter-spacing: -0.01em; }
        p, label, .stMarkdown { color: var(--charcoal); }
        .pla-header {
            font-size: 0.78rem;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            color: var(--accent);
            font-weight: 600;
            margin-bottom: 0.25rem;
        }
        .pla-title {
            font-size: 1.9rem;
            color: var(--navy);
            font-weight: 700;
            margin-top: 0;
        }
        .step-breadcrumb {
            display: flex;
            gap: 0.5rem;
            margin: 1.25rem 0 2rem 0;
            font-size: 0.85rem;
        }
        .step-item {
            padding: 0.35rem 0.9rem;
            border-radius: 999px;
            border: 1px solid #E0E0E0;
            color: #999999;
        }
        .step-item.active {
            background-color: var(--navy);
            color: white;
            border-color: var(--navy);
        }
        .step-item.done {
            border-color: var(--accent);
            color: var(--accent);
        }
        .missing-field {
            border-left: 3px solid #C0392B;
            padding-left: 0.6rem;
            background-color: #FDF3F2;
        }
        .calc-box {
            background-color: #F6F5F2;
            border: 1px solid #E5E2D8;
            border-radius: 6px;
            padding: 1rem 1.2rem;
            margin-top: 0.5rem;
        }
        .stButton>button {
            background-color: var(--navy);
            color: white;
            border-radius: 4px;
            border: none;
            padding: 0.5rem 1.4rem;
        }
        .stButton>button:hover { background-color: var(--accent); color: white; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_breadcrumb(current_step: int) -> None:
    items = []
    for i, label in enumerate(STEP_LABELS, start=1):
        cls = "step-item"
        if i == current_step:
            cls += " active"
        elif i < current_step:
            cls += " done"
        items.append(f'<div class="{cls}">{i}. {label}</div>')
    st.markdown(f'<div class="step-breadcrumb">{"".join(items)}</div>', unsafe_allow_html=True)
    st.progress((current_step - 1) / (len(STEP_LABELS) - 1))


# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------

def init_state() -> None:
    defaults: dict[str, Any] = {
        "step": 1,
        "pla_type": None,  # "XL" | "PROPORTIONAL"
        "source_text": "",
        "parsed_docs": [],
        "extraction_error": None,
        "confirmed_fields": {},
        "treaty_type": None,  # "Surplus" | "Quota Share" | "XL"
        "treaty_params": {},
        "calc_result": None,
        "generated_file": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def go_to(step: int) -> None:
    st.session_state["step"] = step
    st.rerun()


# --------------------------------------------------------------------------
# Step 1: Upload & Select Type
# --------------------------------------------------------------------------

def step_upload() -> None:
    st.subheader("1. Upload claim document(s) and select the PLA template")

    pla_type_label = st.radio(
        "PLA template",
        options=["Non-proportional (XL) PLA", "Proportional PLA"],
        horizontal=True,
    )
    pla_type = "XL" if pla_type_label.startswith("Non-proportional") else "PROPORTIONAL"

    uploaded_files = st.file_uploader(
        "Insurer claim document(s)",
        type=["pdf", "docx"],
        accept_multiple_files=True,
    )

    col_a, col_b = st.columns([1, 5])
    with col_a:
        proceed = st.button("Continue", type="primary")

    if proceed:
        if not uploaded_files:
            st.error("Please upload at least one PDF or Word document.")
            return

        files_for_parsing = [(f, f.name) for f in uploaded_files]
        parsed = parse_documents(files_for_parsing)

        errors = [p for p in parsed if not p.ok]
        scanned = [p for p in parsed if p.ok and p.possibly_scanned]

        for p in errors:
            st.error(f"{p.filename}: {p.error}")
        for p in scanned:
            st.warning(
                f"{p.filename}: very little text was found -- this document may be scanned "
                "and could need OCR before extraction will work well."
            )

        if errors and len(errors) == len(parsed):
            return  # nothing usable to proceed with

        st.session_state["pla_type"] = pla_type
        st.session_state["parsed_docs"] = parsed
        st.session_state["source_text"] = combine_texts(parsed)
        st.session_state["extraction_error"] = None
        go_to(2)


# --------------------------------------------------------------------------
# Step 2: Extraction & Review
# --------------------------------------------------------------------------

XL_FIELD_LABELS = [
    ("treaty_type", "Treaty Type", "text"),
    ("name_of_insured", "Name of Insured", "text"),
    ("policy_no", "Policy No.", "text"),
    ("claim_no", "Claim No.", "text"),
    ("date_of_loss", "Date of Loss", "text"),
    ("period_of_insurance", "Period of Insurance", "text"),
    ("sum_insured", "Sum Insured", "number"),
    ("sum_insured_currency", "Sum Insured Currency", "text"),
    ("particulars_of_loss", "Particulars of Loss", "area"),
    ("description_of_risk_covered", "Description of Risk Covered", "area"),
    ("estimated_amount_of_loss", "Estimated Amount of Loss", "number"),
    ("estimated_amount_of_loss_currency", "Estimated Amount of Loss Currency", "text"),
    ("deductible", "Deductible", "number"),
    ("estimated_amount_affected_to_xl_cover", "Estimated Amount of Loss affected to XL Cover (layer-wise)", "area"),
    ("brief_description_of_loss", "Brief Description of Loss", "area"),
]

PROPORTIONAL_FIELD_LABELS = [
    ("treaty_uw_year", "Treaty / U.W. Year", "text"),
    ("name_of_insured", "Name of Insured", "text"),
    ("policy_no", "Policy No.", "text"),
    ("claim_no", "Claim No.", "text"),
    ("date_of_loss", "Date of Loss", "text"),
    ("period_of_insurance", "Period of Insurance", "text"),
    ("sum_insured", "Sum Insured", "number"),
    ("sum_insured_currency", "Sum Insured Currency", "text"),
    ("particulars_of_loss", "Particulars of Loss", "area"),
    ("description_of_risk_covered", "Description of Risk Covered", "area"),
    ("estimated_amount_of_loss", "Estimated Amount of Loss", "number"),
    ("estimated_amount_of_loss_currency", "Estimated Amount of Loss Currency", "text"),
    ("brief_description_of_loss", "Brief Description of Loss", "area"),
    # retained_loss / estimated_amount_affected_to_treaty are calculated in Step 3, not extracted.
]


def _field_labels_for(pla_type: str) -> list[tuple[str, str, str]]:
    return XL_FIELD_LABELS if pla_type == "XL" else PROPORTIONAL_FIELD_LABELS


def step_extraction_review() -> None:
    st.subheader("2. Review extracted fields")

    pla_type = st.session_state["pla_type"]

    if not st.session_state["confirmed_fields"]:
        with st.spinner("Extracting fields with Gemini..."):
            result = extract_fields(st.session_state["source_text"], pla_type)
        if result.error and not result.used_fallback_raw:
            st.session_state["extraction_error"] = result.error
        elif result.used_fallback_raw:
            st.session_state["extraction_error"] = result.error
        st.session_state["confirmed_fields"] = result.fields.model_dump()
        st.session_state["_raw_extraction_output"] = result.raw_output

    if st.session_state.get("extraction_error"):
        st.error(st.session_state["extraction_error"])
        with st.expander("Raw model output"):
            st.code(st.session_state.get("_raw_extraction_output", ""))

    review_col, source_col = st.columns([3, 2])
    fields = st.session_state["confirmed_fields"]
    labels = _field_labels_for(pla_type)

    with review_col:
        for key, label, kind in labels:
            value = fields.get(key)
            is_missing = value is None or value == ""
            wrapper_class = "missing-field" if is_missing else ""
            st.markdown(f'<div class="{wrapper_class}">', unsafe_allow_html=True)
            display_label = f"{label}  \u26A0" if is_missing else label
            if kind == "number":
                fields[key] = st.number_input(
                    display_label, value=float(value) if value not in (None, "") else 0.0, key=f"field_{key}"
                )
            elif kind == "area":
                fields[key] = st.text_area(display_label, value=value or "", key=f"field_{key}", height=80)
            else:
                fields[key] = st.text_input(display_label, value=value or "", key=f"field_{key}")
            st.markdown("</div>", unsafe_allow_html=True)

    with source_col:
        st.markdown("**Source document text**")
        st.text_area(
            "For verification against the fields on the left",
            value=st.session_state["source_text"],
            height=560,
            disabled=True,
        )

    st.divider()
    back_col, next_col = st.columns([1, 1])
    with back_col:
        if st.button("Back"):
            go_to(1)
    with next_col:
        if st.button("Continue", type="primary"):
            st.session_state["confirmed_fields"] = fields
            go_to(3)


# --------------------------------------------------------------------------
# Step 3: Treaty Details
# --------------------------------------------------------------------------

def step_treaty_details() -> None:
    st.subheader("3. Enter treaty parameters and review the calculation")

    fields = st.session_state["confirmed_fields"]
    loss = float(fields.get("estimated_amount_of_loss") or 0.0)
    sum_insured = float(fields.get("sum_insured") or 0.0)

    st.markdown(f"**Estimated Amount of Loss:** {loss:,.2f}  |  **Sum Insured:** {sum_insured:,.2f}")

    treaty_type = st.selectbox("Treaty type", ["Surplus", "Quota Share", "XL"])
    st.session_state["treaty_type"] = treaty_type

    calc_result = None
    error = None

    try:
        calculator = get_calculator(treaty_type)

        if treaty_type == "Surplus":
            retention_line = st.number_input("Retention Line", min_value=0.0, value=0.0, step=1000.0)
            sum_insured_input = st.number_input(
                "Sum Insured (confirm/edit)", min_value=0.0, value=sum_insured, step=1000.0
            )
            if st.button("Calculate", type="primary"):
                calc_result = calculator.calculate(loss, retention_line, sum_insured_input)

        elif treaty_type == "Quota Share":
            cession_pct_input = st.number_input(
                "Cession % (0-100)", min_value=0.0, max_value=100.0, value=0.0, step=1.0
            )
            if st.button("Calculate", type="primary"):
                calc_result = calculator.calculate(loss, cession_pct_input / 100.0)

        else:  # XL
            num_layers = st.number_input("Number of layers", min_value=1, max_value=10, value=1, step=1)
            layers = []
            for i in range(int(num_layers)):
                c1, c2 = st.columns(2)
                with c1:
                    ded = st.number_input(f"Layer {i + 1} - Deductible", min_value=0.0, value=0.0, step=1000.0, key=f"ded_{i}")
                with c2:
                    lim = st.number_input(f"Layer {i + 1} - Layer Limit", min_value=0.0, value=0.0, step=1000.0, key=f"lim_{i}")
                layers.append(XLLayerInput(deductible=ded, layer_limit=lim))
            if st.button("Calculate", type="primary"):
                calc_result = calculator.calculate(loss, layers)

    except TreatyCalculationError as exc:
        error = str(exc)

    if error:
        st.error(error)

    if calc_result is not None:
        st.session_state["calc_result"] = calc_result

    result = st.session_state.get("calc_result")
    if result is not None:
        st.markdown('<div class="calc-box">', unsafe_allow_html=True)
        st.markdown("**Calculated figures** _(computed, not extracted)_")
        if treaty_type in ("Surplus", "Quota Share"):
            st.write(f"Cession %: {result.cession_pct * 100:.2f}%")
            st.write(f"Retained Loss: {result.retained_loss:,.2f}")
            st.write(f"Amount Affected to Treaty: {result.amount_affected_to_treaty:,.2f}")
        else:
            st.write(f"Retained Loss: {result.retained_loss:,.2f}")
            for layer in result.layers:
                st.write(
                    f"Layer {layer.layer_number} (Deductible {layer.deductible:,.2f} / "
                    f"Limit {layer.layer_limit:,.2f}): Amount Affected = {layer.amount_affected:,.2f}"
                )
            st.write(f"**Total Amount Affected to XL Cover: {result.total_amount_affected:,.2f}**")
        st.markdown("</div>", unsafe_allow_html=True)

    st.divider()
    back_col, next_col = st.columns([1, 1])
    with back_col:
        if st.button("Back", key="back_step3"):
            go_to(2)
    with next_col:
        if st.button("Continue", type="primary", key="next_step3", disabled=result is None):
            go_to(4)


# --------------------------------------------------------------------------
# Step 4: Generate & Download
# --------------------------------------------------------------------------

def _build_render_context() -> dict[str, Any]:
    fields = dict(st.session_state["confirmed_fields"])
    pla_type = st.session_state["pla_type"]
    treaty_type = st.session_state["treaty_type"]
    result = st.session_state["calc_result"]

    if pla_type == "XL":
        fields["deductible"] = result.retained_loss  # first-layer deductible = retained
        layer_lines = "; ".join(
            f"Layer {l.layer_number}: {l.amount_affected:,.2f}" for l in result.layers
        )
        fields["estimated_amount_affected_to_xl_cover"] = layer_lines
    else:
        fields["retained_loss"] = result.retained_loss
        fields["estimated_amount_affected_to_treaty"] = result.amount_affected_to_treaty

    return fields


def step_generate_download() -> None:
    st.subheader("4. Generate and download the PLA")

    pla_type = st.session_state["pla_type"]
    fields = st.session_state["confirmed_fields"]
    result = st.session_state["calc_result"]

    st.markdown("**Summary**")
    summary_col1, summary_col2 = st.columns(2)
    with summary_col1:
        st.write(f"Insured: {fields.get('name_of_insured') or '—'}")
        st.write(f"Claim No.: {fields.get('claim_no') or '—'}")
        st.write(f"Date of Loss: {fields.get('date_of_loss') or '—'}")
    with summary_col2:
        st.write(f"Treaty type: {st.session_state['treaty_type']}")
        if pla_type == "XL":
            st.write(f"Retained Loss: {result.retained_loss:,.2f}")
            st.write(f"Total Amount Affected to XL Cover: {result.total_amount_affected:,.2f}")
        else:
            st.write(f"Retained Loss: {result.retained_loss:,.2f}")
            st.write(f"Amount Affected to Treaty: {result.amount_affected_to_treaty:,.2f}")

    if st.button("Generate document", type="primary"):
        try:
            context = _build_render_context()
            buffer = render_pla(pla_type, context)
            st.session_state["generated_file"] = buffer.getvalue()
        except Exception as exc:  # noqa: BLE001 - surfaced to UI
            st.error(f"Failed to generate the document: {exc}")

    if st.session_state.get("generated_file"):
        insured = (fields.get("name_of_insured") or "claim").replace(" ", "_")
        st.download_button(
            "Download PLA (.docx)",
            data=st.session_state["generated_file"],
            file_name=f"PLA_{insured}.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    st.divider()
    if st.button("Back"):
        go_to(3)
    if st.button("Start a new PLA"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> None:
    inject_css()
    init_state()

    st.markdown('<div class="pla-header">Afro-Asian Reinsurance Brokers</div>', unsafe_allow_html=True)
    st.markdown('<p class="pla-title">PLA Assistant</p>', unsafe_allow_html=True)

    render_breadcrumb(st.session_state["step"])

    step = st.session_state["step"]
    if step == 1:
        step_upload()
    elif step == 2:
        step_extraction_review()
    elif step == 3:
        step_treaty_details()
    elif step == 4:
        step_generate_download()


if __name__ == "__main__":
    main()
