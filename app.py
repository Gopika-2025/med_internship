# app.py
# -------------------------------------------------------------------
# Streamlit App: Medical Report Assistant + ML Model (Git LFS Compatible)
# - Reads PDF/DOCX/Image/TXT
# - Extracts text via PyPDF or OCR
# - Applies rule-based NLP (spaCy + Negex)
# - Loads heavy ML model via pickle (supports Git LFS)
# -------------------------------------------------------------------

import os, io, re, sys, pathlib, pickle
from typing import List, Dict, Any, Optional
from datetime import datetime
import streamlit as st
st.set_page_config(page_title="Medical Report Assistant", layout="wide")

# -------------------------------------------------------------------
# Imports
# -------------------------------------------------------------------
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
from spacy.language import Language
try:
    from negspacy.negation import Negex
    _HAS_NEGEX = True
except Exception:
    _HAS_NEGEX = False

# -------------------------------------------------------------------
# Paths and Globals
# -------------------------------------------------------------------
ROOT = pathlib.Path(__file__).resolve().parent
_NLP: Optional[Language] = None
_RULES: Optional[Dict[str, Any]] = None
MODEL = None

# -------------------------------------------------------------------
# Load Model (supports Git LFS)
# -------------------------------------------------------------------
MODEL_PATH = ROOT / "model.pkl"
if MODEL_PATH.exists():
    try:
        with open(MODEL_PATH, "rb") as f:
            MODEL = pickle.load(f)
        st.sidebar.success("✅ Model loaded successfully from Git LFS.")
    except Exception as e:
        st.sidebar.warning(f"⚠️ Model found but failed to load: {e}")
else:
    st.sidebar.info("ℹ️ No model.pkl found — skipping ML prediction.")

# -------------------------------------------------------------------
# Default Rules
# -------------------------------------------------------------------
_DEFAULT_RULES = {
    "general_rules": {"red_flags": ["sepsis", "shock", "loss of consciousness", "acute abdomen", "chest pain"]},
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

# -------------------------------------------------------------------
# Load NLP + Rules
# -------------------------------------------------------------------
def ensure_models_loaded():
    global _NLP, _RULES
    if _RULES is None:
        rules_path = ROOT / "rules.yaml"
        if rules_path.exists():
            _RULES = yaml.safe_load(open(rules_path, "r", encoding="utf-8"))
        else:
            _RULES = _DEFAULT_RULES

    if _NLP is None:
        try:
            _NLP = spacy.load("en_core_web_sm")
        except OSError:
            import spacy.cli
            spacy.cli.download("en_core_web_sm")
            _NLP = spacy.load("en_core_web_sm")

        if _HAS_NEGEX and "negex" not in _NLP.pipe_names:
            _NLP.add_pipe("negex")

# -------------------------------------------------------------------
# Helper Functions
# -------------------------------------------------------------------
def _text_to_pdf(text: str, out_path: str) -> str:
    c = canvas.Canvas(out_path, pagesize=A4)
    width, height = A4
    y = height - 20 * mm
    c.setFont("Helvetica", 11)
    for line in text.splitlines():
        if y < 20 * mm:
            c.showPage()
            y = height - 20 * mm
            c.setFont("Helvetica", 11)
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
        text = "\n".join(p.text for p in Document(input_path).paragraphs if p.text.strip())
        return _text_to_pdf(text, out_pdf)
    if input_path.lower().endswith(".txt"):
        text = open(input_path, "r", encoding="utf-8", errors="ignore").read()
        return _text_to_pdf(text, out_pdf)
    return out_pdf

def extract_text_from_pdf(pdf_path: str) -> str:
    ensure_models_loaded()
    embedded = []
    try:
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            t = page.extract_text() or ""
            if t.strip():
                embedded.append(t.strip())
    except Exception:
        pass
    text = "\n".join(embedded)
    if len(text) >= 200 or not _HAS_PDF2IMAGE:
        return text
    try:
        pages = convert_from_path(pdf_path, dpi=200)
        ocr_texts = [pytesseract.image_to_string(img) for img in pages]
        return text + "\n" + "\n".join(ocr_texts)
    except Exception:
        return text

def extract_entities(text: str) -> List[str]:
    ensure_models_loaded()
    doc = _NLP(text)
    ents = []
    for ent in getattr(doc, "ents", []):
        neg = getattr(ent._, "negex", False) if _HAS_NEGEX else False
        if not neg:
            ents.append(ent.text.strip())
    return list(set(ents))

def match_condition(text: str) -> Dict[str, Any]:
    low = text.lower()
    for dis in _RULES.get("diseases", []):
        for kw in dis.get("keywords", []):
            if kw.lower() in low:
                return dis
    return {}

def severity_and_cost(dis: Dict[str, Any], text: str, tier: str):
    red_flags = []
    for rf in _RULES["general_rules"]["red_flags"]:
        if re.search(rf, text, re.IGNORECASE):
            red_flags.append(rf)
    for rf in dis.get("severity_rules", {}).get("red_flags", []):
        if re.search(rf, text, re.IGNORECASE):
            red_flags.append(rf)
    if red_flags:
        band, score = "red", 0.9
    elif dis:
        band, score = "amber", 0.6
    else:
        band, score = "green", 0.3
    cost = dis.get("cost_band", {}).get(f"tier_{tier}", [0, 0])
    return band, score, red_flags, cost

# -------------------------------------------------------------------
# Streamlit UI
# -------------------------------------------------------------------
st.title("🩺 Medical Report Assistant + ML Model")
st.caption("Rule-based and ML-powered report analyzer")

with st.sidebar:
    st.header("⚙️ Settings")
    city = st.text_input("City", "Chennai")
    tier = st.selectbox("Hospital Tier", ["1", "2", "3"], index=1)
    out_name = st.text_input("Summary PDF name", "summary.pdf")

uploaded = st.file_uploader("📂 Upload Report", type=["pdf", "docx", "png", "jpg", "jpeg", "txt"])

if uploaded:
    st.success(f"✅ Uploaded: {uploaded.name}")
    tmp_in = f"./_tmp_{uploaded.name}"
    with open(tmp_in, "wb") as f:
        f.write(uploaded.getbuffer())

    if st.button("🔍 Analyze Report"):
        with st.spinner("Processing..."):
            pdf_path = convert_to_pdf(tmp_in)
            raw_text = extract_text_from_pdf(pdf_path)
            entities = extract_entities(raw_text)
            dis = match_condition(raw_text)
            band, score, red_flags, cost = severity_and_cost(dis, raw_text, tier)

        st.subheader("🧾 Summary")
        st.write(f"**Detected Condition:** {dis.get('name', 'Unknown')}")
        st.write(f"**Severity:** {band.upper()} (score {score:.2f})")
        st.write(f"**Red Flags:** {', '.join(red_flags) or 'None'}")
        st.write(f"**Entities:** {', '.join(entities) or '—'}")
        st.write(f"**Procedures:** {', '.join(dis.get('procedures', [])) or '—'}")
        st.write(f"**Recovery:** {', '.join(dis.get('recovery_recos', [])) or '—'}")
        st.write(f"**Estimated Cost (₹):** {cost[0]} — {cost[1]}")

        # Optional model prediction
        if MODEL:
            try:
                X = [[len(raw_text) % 10, len(entities)]]
                pred = MODEL.predict(X)
                st.success(f"🤖 Model Prediction: {pred[0]}")
            except Exception as e:
                st.warning(f"⚠️ Model prediction failed: {e}")

        summary_text = (
            f"City: {city}\nCondition: {dis.get('name','Unknown')}\n"
            f"Severity: {band}\nRed Flags: {', '.join(red_flags)}\nEntities: {', '.join(entities)}\n"
        )
        pdf_out = "summary.pdf"
        _text_to_pdf(summary_text, pdf_out)
        with open(pdf_out, "rb") as f:
            st.download_button("⬇️ Download Summary PDF", f, file_name=out_name, mime="application/pdf")

else:
    st.info("📤 Upload a medical report to start.")
