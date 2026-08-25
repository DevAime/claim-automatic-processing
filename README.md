# PLA Assistant

A Streamlit tool for Afro-Asian Reinsurance Brokers that drafts Preliminary
Loss Advices (PLAs) by extracting structured claim data from insurer
documents (PDF/Word) with Gemini, applying deterministic treaty-type
calculations in pure Python, and generating the final Word document.

## Project layout

```
pla_assistant/
├── app.py                    # Streamlit UI wiring (4-step wizard) only
├── document_parsing.py       # PDF/Word -> raw text
├── extraction.py             # Gemini calls + Pydantic schemas
├── calculators.py            # Treaty math (Surplus / Quota Share / XL) — no LLM
├── document_generation.py    # Fills the docxtpl templates
├── templates/
│   ├── xl_pla_template.docx
│   └── proportional_pla_template.docx
├── scripts/
│   └── build_templates.py    # Regenerates the two templates above, if needed
├── requirements.txt
└── .env.example
```

## 1. Set up the environment

Requires Python 3.11+.

```bash
cd pla_assistant
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Add your Gemini API key

```bash
cp .env.example .env
```

Open `.env` and set:

```
GEMINI_API_KEY=your-actual-key-here
```

Get a key from [Google AI Studio](https://aistudio.google.com/apikey) if you
don't have one yet.

## 3. Run the app

```bash
streamlit run app.py
```

This opens the app in your browser, normally at `http://localhost:8501`.
Streamlit reloads automatically whenever you edit and save a `.py` file, so
you can leave it running while you tweak things.

## 4. Using it

1. **Upload & Select Type** — upload one or more claim documents (PDF or
   Word) and choose whether this is a Non-proportional (XL) or Proportional
   PLA.
2. **Extraction & Review** — Gemini extracts the fields; anything it
   couldn't find is flagged with a warning icon and a red left border so you
   know exactly what to fill in by hand. The original document text is shown
   alongside for cross-checking.
3. **Treaty Details** — pick Surplus, Quota Share, or XL, enter the
   treaty-specific numbers (Retention Line / Cession % / Deductible & Layer
   Limit per layer), and see the calculated Retained Loss and Amount
   Affected to Treaty before continuing. These figures are always computed
   in plain Python — Gemini never touches them.
4. **Generate & Download** — review the summary and download the finished
   `.docx`.

Click **"Start a new PLA"** at the end to reset and process another claim.

## Notes

- Treaty terms (Retention Line, Cession %, Deductible, Layer Limits) are
  entered manually each session — there's no database yet, as specified.
- If a PDF comes back with almost no extractable text, the app flags it as
  possibly scanned; run OCR on it separately before uploading, since
  pdfplumber can't read image-only pages.
- If Gemini's JSON response doesn't validate against the expected schema,
  the app retries once automatically, then falls back to showing you the
  raw output rather than crashing.
- To change the look of the generated Word document, edit and re-run
  `scripts/build_templates.py`, or edit the `.docx` files in `templates/`
  directly in Word (the `{{ field_name }}` placeholders must stay intact).

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "No Gemini API key found" | `.env` missing or `GEMINI_API_KEY` not set — see step 2 |
| Fields all come back empty | Uploaded PDF is likely scanned/image-only — check the warning banner |
| "Gemini's output did not match the expected schema" | Rare model formatting slip; try re-uploading, or check the raw output shown in the expander |
| `ModuleNotFoundError` on launch | Virtual env not activated, or `pip install -r requirements.txt` not run |
