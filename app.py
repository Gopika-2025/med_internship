# app.py
# -------------------------------------------------------------------
# Streamlit App: Offline Medical Report Assistant
# - Converts input (PDF/DOCX/Image/TXT) to canonical PDF
# - Extracts text using pypdf or Tesseract OCR
# - NLP: spaCy/scispaCy + Negex
# - Rule-based triage: disease detection, severity scoring
# - Summary PDF + cost estimation + optional ML model
# -------------------------------------------------------------------

# ---------------------------
# 1️⃣ Imports (no Streamlit commands yet)
# ---------------------------
import os, sys, pathlib, io, re
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, date, time, timedelta
from urllib.parse import quote
import pickle

import streamlit as st  # ✅ Import before using Streamlit commands

# ✅ Must be FIRST Streamlit command
st.set_page_config(page_title="Medical Report Assistant", layout="wide")

# ---------------------------
# 2️⃣ Continue imports
# ---------------------------
import yaml
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from pypdf import PdfReader
from PIL import Image
import pytesseract

# Optional: OCR fallback
try:
    from pdf2image import convert_from_path
    _HAS_PDF2IMAGE = True
except Exception:
    _HAS_PDF2IMAGE = False

# NLP
import spacy
from spacy.language import Language
from spacy.pipeline import EntityRuler
try:
    from negspacy.negation import Negex
    _HAS_NEGEX = True
except Exception:
    _HAS_NEGEX = False

# ---------------------------
# 3️⃣ Global Variables
# ---------------------------
ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_NLP: Optional[Language] = None
_RULES: Optional[Dict[str, Any]] = None

# ---------------------------
# 4️⃣ Load model.pkl (optional)
# ---------------------------
MODEL = None
MODEL_PATH = ROOT / "model.pkl"
if MODEL_PATH.exists():
    try:
        with open(MODEL_PATH, "rb") as f:
            MODEL = pickle.load(f)
    except Exception as e:
        st.sidebar.warning(f"⚠️ Could not load model.pkl: {e}")

# ---------------------------
# 5️⃣ Default Rule Definitions
# ---------------------------
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

# ---------------------------
# 6️⃣ Helper Functions
# ---------------------------
def ensure_models_loaded():
    """Load NLP and rules safely."""
    global _NLP, _RULES
    if _RULES is None:
        rules_path = ROOT / "rules.yaml"
        if rules_path.exists():
            with open(rules_path, "r", encoding="utf-8") as f:
                _RULES = yaml.safe_load(f)
        else:
            _RULES = _DEFAULT_RULES
    if _NLP is None:
        try:
            _NLP = spacy.load("en_core_web_sm")
        except Exception:
            _NLP = spacy.blank("en")
            _add_entity_ruler_from_rules(_NLP, _RULES)
        if _HAS_NEGEX and "negex" not in _NLP.pipe_names:
            _NLP.add_pipe("negex")

def _add_entity_ruler_from_rules(nlp: Language, rules: Dict[str, Any]) -> None:
    ruler = nlp.add_pipe("entity_ruler")
    patterns = []
    for dis in rules.get("diseases", []):
        for kw in dis.get("keywords", []):
            patterns.append({"label": "CONDITION", "pattern": kw})
    ruler.add_patterns(patterns)

def _text_to_pdf(text: str, out_path: str) -> str:
    c = canvas.Canvas(out_path, pagesize=A4)
    width, height = A4
    y = height - 20 * mm
    c.setFont("Helvetica", 11)
    for line in text.splitlines():
        if y < 20 * mm:
            c.showPage()
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

def extract_positive_entities(text: str) -> List[str]:
    ensure_models_loaded()
    doc = _NLP(text)
    ents = []
    for ent in getattr(doc, "ents", []):
        neg = getattr(ent._, "negex", False) if _HAS_NEGEX else False
        if not neg:
            ents.append(ent.text.strip())
    return list(set(ents))

def _match_condition(text: str) -> Dict[str, Any]:
    low = text.lower()
    for dis in _RULES.get("diseases", []):
        for kw in dis.get("keywords", []):
            if kw.lower() in low:
                return dis
    return {}

def _severity_for(dis: Dict[str, Any], text: str) -> Tuple[str, float, List[str], List[str]]:
    reasons, red_flags = [], []
    if dis:
        for kw in dis.get("keywords", []):
            if re.search(r"\b" + re.escape(kw) + r"\b", text, re.IGNORECASE):
                reasons.append(f"Matched keyword: {kw}")
        for rf in _RULES["general_rules"]["red_flags"]:
            if re.search(rf, text, re.IGNORECASE):
                red_flags.append(rf)
        for rf in dis.get("severity_rules", {}).get("red_flags", []):
            if re.search(rf, text, re.IGNORECASE):
                red_flags.append(rf)
    if red_flags:
        return "red", 0.9, red_flags, reasons + ["Red flags present"]
    elif dis:
        return "amber", 0.6, red_flags, reasons
    else:
        return "green", 0.3, [], ["No significant findings"]

def _cost_for(dis: Dict[str, Any], tier: str) -> Tuple[int, int]:
    if not dis:
        return (0, 0)
    return tuple(dis.get("cost_band", {}).get(f"tier_{tier}", [0, 0]))

# ---------------------------
# 7️⃣ Streamlit UI
# ---------------------------
st.title("🩺 Medical Report Assistant — Offline Mode")
st.caption("Uses OCR + NLP for offline triage & insights. No external APIs required.")

with st.sidebar:
    st.header("⚙️ Settings")
    city = st.text_input("City", "Chennai")
    tier = st.selectbox("Hospital tier", ["1", "2", "3"], index=1)
    out_name = st.text_input("Summary PDF name", "summary.pdf")

uploaded = st.file_uploader("📂 Upload Report", type=["pdf", "docx", "png", "jpg", "jpeg", "txt"])

if uploaded:
    st.success(f"✅ Loaded: {uploaded.name}")
    tmp_in = f"./_tmp_{uploaded.name}"
    with open(tmp_in, "wb") as f:
        f.write(uploaded.getbuffer())

    if st.button("🔍 Analyze Report"):
        with st.spinner("Processing..."):
            result = {
                "city": city,
                "tier": tier,
                **process_report(tmp_in, city, tier),
            }

        st.subheader("🧾 Summary")
        st.write(f"**Detected Condition:** {result['disease']}")
        st.write(f"**Severity:** {result['severity_band']} (score {result['severity_score']:.2f})")
        st.write(f"**Key Findings:** {', '.join(result['findings']) or 'None'}")
        st.write(f"**Procedures:** {', '.join(result['procedures']) or '—'}")
        st.write(f"**Recovery:** {', '.join(result['recovery']) or '—'}")
        st.write(f"**Cost Estimate (₹):** {result['cost_range'][0]} — {result['cost_range'][1]}")

        if MODEL:
            try:
                X = [[len(result["raw_text"]) % 10, len(result["findings"])]]
                pred = MODEL.predict(X)
                st.info(f"🤖 Model Prediction: {pred[0]}")
            except Exception as e:
                st.warning(f"Model prediction failed: {e}")

        pdf_path = _text_to_pdf(result["raw_text"][:1000], "summary.pdf")
        with open(pdf_path, "rb") as f:
            st.download_button("⬇️ Download Summary", f, file_name=out_name, mime="application/pdf")
else:
    st.info("📤 Upload a report to begin.")
