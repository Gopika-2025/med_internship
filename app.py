# app.py
# -------------------------------------------------------------------
# Streamlit App: Offline Medical Report Assistant (Cloud-Ready)
# - Converts input (PDF/DOCX/Image/TXT) to canonical PDF
# - Extracts text using PyPDF or Tesseract OCR
# - NLP: spaCy (en_core_web_sm) + optional Negex
# - Rule-based triage: condition detection, severity scoring
# -------------------------------------------------------------------

import os, io, re, pathlib
from typing import List, Tuple, Dict, Any, Optional

import streamlit as st
st.set_page_config(page_title="Medical Report Assistant", layout="wide")

import yaml
from docx import Document
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from pypdf import PdfReader
from PIL import Image
import pytesseract

# Optional OCR fallback
try:
    from pdf2image import convert_from_path
    _HAS_PDF2IMAGE = True
except Exception:
    _HAS_PDF2IMAGE = False

# NLP setup
import spacy
import spacy.cli
from spacy.language import Language

try:
    from negspacy.negation import Negex
    _HAS_NEGEX = True
except Exception:
    _HAS_NEGEX = False

# Ensure spaCy model is available
try:
    _NLP = spacy.load("en_core_web_sm")
except OSError:
    spacy.cli.download("en_core_web_sm")
    _NLP = spacy.load("en_core_web_sm")
if _HAS_NEGEX and "negex" not in _NLP.pipe_names:
    _NLP.add_pipe("negex")

# ---------------------------
# Default rule definitions
# ---------------------------
_DEFAULT_RULES = {
    "general_rules": {
        "red_flags": ["sepsis", "shock", "loss of consciousness", "acute abdomen", "chest pain"]
    },
    "diseases": [
        {
            "name": "Appendicitis",
            "keywords": ["appendix", "appendicitis", "RLQ pain", "right lower quadrant"],
            "severity_rules": {"red_flags": ["perforated", "abscess", "peritonitis"]},
            "procedures": ["Laparoscopic appendectomy"],
            "recovery_recos": ["Rest 2–3 weeks", "Avoid heavy lifting for 2 weeks"],
            "cost_band": {"tier_1": [60000, 90000], "tier_2": [40000, 70000], "tier_3": [30000, 50000]},
        },
        {
            "name": "Gallstones",
            "keywords": ["gallbladder", "cholelithiasis", "biliary colic", "GB stone"],
            "severity_rules": {"red_flags": ["cholangitis", "bile duct obstruction", "pancreatitis"]},
            "procedures": ["Laparoscopic cholecystectomy"],
            "recovery_recos": ["Low-fat diet", "Desk work in ~2 weeks"],
            "cost_band": {"tier_1": [70000, 110000], "tier_2": [50000, 85000], "tier_3": [35000, 65000]},
        },
    ],
}

ROOT = pathlib.Path(__file__).resolve().parent
_RULES: Optional[Dict[str, Any]] = None

def load_rules() -> Dict[str, Any]:
    """Load rules from YAML if present, else defaults."""
    global _RULES
    if _RULES is not None:
        return _RULES
    rules_path = ROOT / "rules.yaml"
    if rules_path.exists():
        with open(rules_path, "r", encoding="utf-8") as f:
            _RULES = yaml.safe_load(f)
    else:
        _RULES = _DEFAULT_RULES
    return _RULES

# ---------------------------
# Utilities
# ---------------------------
def _text_to_pdf(text: str, out_path: str) -> str:
    c = canvas.Canvas(out_path, pagesize=A4)
    width, height = A4
    y = height - 20 * mm
    c.setFont("Helvetica", 11)
    for line in text.splitlines():
        if y < 20 * mm:
            c.showPage()
            c.setFont("Helvetica", 11)
            y = height - 20 * mm
        c.drawString(20 * mm, y, line[:120])
        y -= 14
    c.save()
    return out_path

def convert_to_pdf(input_path: str) -> str:
    base, _ = os.path.splitext(input_path)
    out_pdf = base + "__canonical.pdf"
    if input_path.lower().endswith(".pdf"):
        with open(input_path, "rb") as fin, open(out_pdf, "wb") as fout:
            fout.write(fin.read())
        return out_pdf
    if input_path.lower().endswith((".png", ".jpg", ".jpeg")):
        with Image.open(input_path) as im:
            if im.mode in ("RGBA", "P"):
                im = im.convert("RGB")
            im.save(out_pdf, "PDF", resolution=200.0)
        return out_pdf
    if input_path.lower().endswith(".docx"):
        text = "\n".join([p.text for p in Document(input_path).paragraphs if p.text.strip()])
        return _text_to_pdf(text, out_pdf)
    if input_path.lower().endswith(".txt"):
        text = open(input_path, "r", encoding="utf-8", errors="ignore").read()
        return _text_to_pdf(text, out_pdf)
    return out_pdf

def extract_text_from_pdf(pdf_path: str) -> str:
    text = ""
    try:
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            t = page.extract_text() or ""
            text += t.strip() + "\n"
    except Exception:
        text = ""
    if len(text) > 200 or not _HAS_PDF2IMAGE:
        return text
    try:
        pages = convert_from_path(pdf_path, dpi=200)
        ocr_texts = [pytesseract.image_to_string(img) for img in pages]
        return text + "\n" + "\n".join(ocr_texts)
    except Exception:
        return text

def extract_entities(text: str) -> List[str]:
    doc = _NLP(text)
    ents = []
    for ent in getattr(doc, "ents", []):
        neg = getattr(ent._, "negex", False) if _HAS_NEGEX else False
        if not neg:
            ents.append(ent.text.strip())
    return list(set(ents))

def detect_condition(text: str, rules: Dict[str, Any]) -> Dict[str, Any]:
    low = text.lower()
    for dis in rules.get("diseases", []):
        for kw in dis.get("keywords", []):
            if kw.lower() in low:
                return dis
    return {}

def assess_severity(dis: Dict[str, Any], text: str, rules: Dict[str, Any]) -> Tuple[str, float, List[str]]:
    red_flags = []
    for rf in rules["general_rules"]["red_flags"]:
        if re.search(rf, text, re.IGNORECASE):
            red_flags.append(rf)
    for rf in dis.get("severity_rules", {}).get("red_flags", []):
        if re.search(rf, text, re.IGNORECASE):
            red_flags.append(rf)
    if red_flags:
        return "red", 0.9, red_flags
    elif dis:
        return "amber", 0.6, []
    return "green", 0.3, []

# ---------------------------
# Streamlit Interface
# ---------------------------
st.title("🩺 Medical Report Assistant")
st.caption("Offline NLP + OCR-based triage (for educational use only).")

with st.sidebar:
    city = st.text_input("City", "Chennai")
    tier = st.selectbox("Hospital Tier", ["1", "2", "3"], index=1)
    out_name = st.text_input("Output PDF name", "summary.pdf")

uploaded = st.file_uploader("📂 Upload a medical report", type=["pdf", "docx", "png", "jpg", "jpeg", "txt"])

if uploaded:
    tmp_file = f"./_tmp_{uploaded.name}"
    with open(tmp_file, "wb") as f:
        f.write(uploaded.getbuffer())
    st.success(f"✅ Uploaded: {uploaded.name}")

    if st.button("🔍 Analyze Report"):
        with st.spinner("Processing..."):
            rules = load_rules()
            pdf_path = convert_to_pdf(tmp_file)
            raw_text = extract_text_from_pdf(pdf_path)
            ents = extract_entities(raw_text)
            dis = detect_condition(raw_text, rules)
            band, score, red_flags = assess_severity(dis, raw_text, rules)
            cost_band = dis.get("cost_band", {}).get(f"tier_{tier}", [0, 0])

        st.subheader("🧾 Summary")
        st.write(f"**Detected Condition:** {dis.get('name', 'Unknown')}")
        st.write(f"**Severity:** {band.upper()} (score {score:.2f})")
        st.write(f"**Red Flags:** {', '.join(red_flags) or 'None'}")
        st.write(f"**Findings (entities):** {', '.join(ents) or '—'}")
        st.write(f"**Procedures:** {', '.join(dis.get('procedures', [])) or '—'}")
        st.write(f"**Recovery Advice:** {', '.join(dis.get('recovery_recos', [])) or '—'}")
        st.write(f"**Estimated Cost (₹):** {cost_band[0]} — {cost_band[1]}")

        summary = (
            f"City: {city}\n"
            f"Detected Condition: {dis.get('name', 'Unknown')}\n"
            f"Severity: {band} ({score})\n"
            f"Red Flags: {', '.join(red_flags)}\n"
            f"Entities: {', '.join(ents)}\n"
        )
        pdf_out = "summary.pdf"
        _text_to_pdf(summary, pdf_out)
        with open(pdf_out, "rb") as f:
            st.download_button("⬇️ Download Summary", f, file_name=out_name, mime="application/pdf")
else:
    st.info("📤 Upload a report to begin.")
