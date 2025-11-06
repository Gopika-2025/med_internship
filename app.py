# app.py
# -------------------------------------------------------------------
# Single-file Streamlit app: Offline Medical Report Assistant (Cloud-Compatible)
# - Canonicalizes input (PDF/DOCX/Image/TXT) to PDF
# - Text extraction via pypdf and pdfplumber
# - OCR fallback via EasyOCR (no Poppler/Tesseract required)
# - NLP: spaCy or scispaCy; Negation via Negex
# - Rule engine: rules.yaml or defaults
# - Output: severity band/score, summary PDF, and calendar links
# -------------------------------------------------------------------

import os, sys, pathlib, io, re, tempfile, json
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime, date, time, timedelta
from urllib.parse import quote

import streamlit as st
import yaml
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm

# PDF text
from pypdf import PdfReader
import pdfplumber
import easyocr
import numpy as np
from PIL import Image

# NLP
import spacy
from spacy.language import Language
from spacy.pipeline import EntityRuler
try:
    from negspacy.negation import Negex
    _HAS_NEGEX = True
except Exception:
    _HAS_NEGEX = False

# --- Path setup ---
ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("STREAMLIT_SERVER_ADDRESS", "127.0.0.1")
os.environ.setdefault("STREAMLIT_SERVER_PORT", "8501")

_NLP: Optional[Language] = None
_RULES: Optional[Dict[str, Any]] = None

# ---------------------------
# Default rules
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
        {
            "name": "Knee Osteoarthritis",
            "keywords": ["knee osteoarthritis", "gonarthrosis", "degenerative knee", "tricompartmental OA"],
            "severity_rules": {"red_flags": ["severe deformity", "night pain unrelieved"]},
            "procedures": [
                "Conservative therapy (PT/IA injections)",
                "Unicompartmental knee replacement (selected)",
                "Total knee replacement (advanced OA)",
            ],
            "recovery_recos": ["Quad strengthening", "Weight management", "Physiotherapy"],
            "cost_band": {"tier_1": [180000, 350000], "tier_2": [120000, 250000], "tier_3": [90000, 180000]},
        },
    ],
}

# ---------------------------
# Rules loading
# ---------------------------
def _load_rules(default_rules: Dict[str, Any], uploaded_rules: Optional[bytes]) -> Dict[str, Any]:
    if uploaded_rules:
        try:
            data = yaml.safe_load(uploaded_rules.decode("utf-8", errors="ignore"))
            if isinstance(data, dict) and "diseases" in data:
                return data
        except Exception:
            pass
    rules_path = ROOT / "rules.yaml"
    if rules_path.exists():
        try:
            with open(rules_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict) and "diseases" in data:
                    return data
        except Exception:
            pass
    return default_rules

# ---------------------------
# NLP loading
# ---------------------------
@st.cache_resource(show_spinner=False)
def ensure_models_loaded(rules_blob: Optional[bytes]) -> Tuple[Language, Dict[str, Any]]:
    rules = _load_rules(_DEFAULT_RULES, rules_blob)
    try:
        nlp = spacy.load("en_ner_bc5cdr_md")
    except Exception:
        try:
            nlp = spacy.load("en_core_web_sm")
        except Exception:
            nlp = spacy.blank("en")
            _add_entity_ruler_from_rules(nlp, rules)

    if _HAS_NEGEX and "negex" not in nlp.pipe_names:
        try:
            nlp.add_pipe("negex")
        except Exception:
            pass

    return nlp, rules

# ---------------------------
# File conversions
# ---------------------------
def convert_to_pdf(input_path: str) -> str:
    base, _ = os.path.splitext(input_path)
    out_pdf = base + "__canonical.pdf"

    if _is_pdf(input_path):
        with open(input_path, "rb") as fin, open(out_pdf, "wb") as fout:
            fout.write(fin.read())
        return out_pdf

    if _is_image(input_path):
        with Image.open(input_path) as im:
            if im.mode in ("RGBA", "P"):
                im = im.convert("RGB")
            im.save(out_pdf, "PDF", resolution=200.0)
        return out_pdf

    if _is_docx(input_path):
        text = "\n".join(p.text for p in Document(input_path).paragraphs)
        return _text_to_pdf(text, out_pdf)

    if _is_txt(input_path):
        with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        return _text_to_pdf(text, out_pdf)

    with open(input_path, "rb") as fin, open(out_pdf, "wb") as fout:
        fout.write(fin.read())
    return out_pdf

def _text_to_pdf(text: str, out_path: str) -> str:
    c = canvas.Canvas(out_path, pagesize=A4)
    width, height = A4
    margin = 20 * mm
    y = height - margin
    c.setFont("Helvetica", 11)
    for line in text.splitlines():
        if y < margin:
            c.showPage()
            c.setFont("Helvetica", 11)
            y = height - margin
        c.drawString(margin, y, line[:120])
        y -= 14
    c.save()
    return out_path

# ---------------------------
# Text extraction (Cloud safe)
# ---------------------------
def extract_text_from_pdf(pdf_path: str, ocr_enabled: bool, ocr_dpi: int) -> str:
    """Extract text: embedded first, OCR fallback via EasyOCR."""
    embedded = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                t = page.extract_text() or ""
                if t.strip():
                    embedded.append(t)
    except Exception:
        embedded = []

    joined = "\n".join(embedded)
    if len(joined) >= 200 or not ocr_enabled:
        return joined

    st.warning("Using EasyOCR fallback (slower, but cloud compatible).")
    reader = easyocr.Reader(['en'], gpu=False)
    ocr_texts = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                img = page.to_image(resolution=ocr_dpi).original
                result = reader.readtext(np.array(img), detail=0)
                ocr_texts.append(" ".join(result))
    except Exception as e:
        st.error(f"OCR error: {e}")
    return joined + "\n" + "\n".join(ocr_texts)

# ---------------------------
# NLP helpers
# ---------------------------
def extract_positive_entities(text: str, nlp: Language) -> List[str]:
    doc = nlp(text)
    ents = []
    for ent in getattr(doc, "ents", []):
        neg = False
        try:
            if _HAS_NEGEX:
                neg = getattr(ent._, "negex", False)
        except Exception:
            neg = False
        if not neg:
            ents.append(ent.text)
    uniq = []
    seen = set()
    for e in ents:
        norm = re.sub(r"\s+", " ", e.strip())
        if norm.lower() not in seen:
            seen.add(norm.lower())
            uniq.append(norm)
    return uniq

def _match_condition(text: str, rules: Dict[str, Any]) -> Dict[str, Any]:
    low = text.lower()
    for dis in rules.get("diseases", []):
        for kw in dis.get("keywords", []):
            if kw.lower() in low:
                return dis
    return {}

def _severity_for(dis: Dict[str, Any], text: str, rules: Dict[str, Any]) -> Tuple[str, float, List[str], List[str]]:
    reasons, red_flags = [], []
    kw_hits = 0
    if dis:
        for kw in dis.get("keywords", []):
            if re.search(r"\b" + re.escape(kw) + r"\b", text, re.IGNORECASE):
                kw_hits += 1
        if kw_hits:
            reasons.append(f"Matched disease keywords: {kw_hits}")

    for rf in rules.get("general_rules", {}).get("red_flags", []):
        if re.search(rf, text, re.IGNORECASE):
            red_flags.append(rf)
    if dis:
        for rf in dis.get("severity_rules", {}).get("red_flags", []):
            if re.search(rf, text, re.IGNORECASE):
                red_flags.append(rf)

    red_flags = sorted(set(red_flags))
    if red_flags:
        reasons.append("Red flags: " + ", ".join(red_flags))
    if red_flags:
        return "red", 0.9, red_flags, reasons
    elif dis:
        return "amber", 0.6, red_flags, reasons
    else:
        return "green", 0.3, red_flags, ["No matching condition or red flags"]

def _cost_for(dis: Dict[str, Any], tier: str) -> Tuple[int, int]:
    if not dis:
        return (0, 0)
    cb = dis.get("cost_band", {})
    rng = cb.get(f"tier_{tier}", [0, 0])
    if len(rng) == 2:
        return int(rng[0]), int(rng[1])
    return (0, 0)

def _add_entity_ruler_from_rules(nlp: Language, rules: Dict[str, Any]) -> None:
    ruler = nlp.add_pipe("entity_ruler")
    patterns = []
    for dis in rules.get("diseases", []):
        for kw in dis.get("keywords", []):
            if kw:
                patterns.append({"label": "CONDITION", "pattern": kw})
    ruler.add_patterns(patterns)

# ---------------------------
# UI helpers
# ---------------------------
def severity_badge(band: str) -> str:
    color = {"red": "#ff4d4f", "amber": "#faad14", "green": "#52c41a"}.get(band.lower(), "#888")
    return f'<span style="background:{color};color:white;padding:4px 10px;border-radius:12px;font-weight:600;">{band.capitalize()}</span>'

# -------------------------------------------------------------------
# Streamlit UI
# -------------------------------------------------------------------
st.set_page_config(page_title="Medical Report Assistant (Cloud-Compatible)", layout="wide")
st.title("🩺 Medical Report Assistant — Cloud-Compatible Version")

st.caption("Local NLP + OCR (no external API). Educational triage — not medical advice.")

with st.sidebar:
    st.header("⚙️ Settings")
    city = st.text_input("City (for cost context)", value="Chennai")
    tier = st.selectbox("Hospital tier", ["1", "2", "3"], index=1)
    out_name = st.text_input("Summary PDF name", value="summary.pdf")
    st.markdown("---")
    ocr_enabled = st.checkbox("Enable OCR fallback", value=True)
    ocr_dpi = st.slider("OCR DPI", min_value=100, max_value=400, step=20, value=200)
    uploaded_rules = st.file_uploader("Upload rules.yaml (optional)", type=["yaml", "yml"])
    st.markdown("---")
    st.write("Upload a report (PDF, DOCX, Image, or TXT), click **Analyze**, and download the results.")

# Initialize models
try:
    with st.spinner("Initializing NLP and rules…"):
        _NLP, _RULES = ensure_models_loaded(uploaded_rules.read() if uploaded_rules else None)
except Exception as e:
    st.error("Startup error. Please check your dependencies.")
    st.exception(e)
    st.stop()

uploaded = st.file_uploader("Upload medical report", type=["pdf", "docx", "png", "jpg", "jpeg", "txt"])
if uploaded:
    st.success(f"Loaded: {uploaded.name}")
    tmp_in = os.path.join(".", f"_tmp_{uploaded.name}")
    with open(tmp_in, "wb") as f:
        f.write(uploaded.getbuffer())

    if st.button("🔍 Analyze Report", type="primary"):
        with st.spinner("Processing report…"):
            result = {}
            try:
                pdf_path = convert_to_pdf(tmp_in)
                raw_text = extract_text_from_pdf(pdf_path, ocr_enabled, ocr_dpi)
                dis = _match_condition(raw_text, _RULES)
                band, score, red_flags, reasons = _severity_for(dis, raw_text, _RULES)
                result = {
                    "city": city, "tier": tier, "disease": dis.get("name", "Unknown"),
                    "severity_band": band, "severity_score": score, "red_flags": red_flags,
                    "severity_reasons": reasons, "procedures": dis.get("procedures", []),
                    "recovery": dis.get("recovery_recos", []), "cost_range": _cost_for(dis, tier),
                    "findings": extract_positive_entities(raw_text, _NLP),
                }
            except Exception as e:
                st.error("Processing error")
                st.exception(e)
                st.stop()

        st.subheader("🧾 Results")
        st.markdown(severity_badge(result["severity_band"]), unsafe_allow_html=True)
        st.write("**Condition:**", result["disease"])
        st.write("**Score:**", result["severity_score"])
        st.write("**Red Flags:**", ", ".join(result["red_flags"]) or "None")
        st.write("**Key Findings:**")
        st.write("\n".join([f"• {f}" for f in (result["findings"] or ["—"])]))
        st.write("**Procedures:**", ", ".join(result["procedures"]) or "—")
        st.write("**Recovery:**", ", ".join(result["recovery"]) or "—")
        min_c, max_c = result["cost_range"]
        st.metric("Estimated Cost (₹)", f"{min_c:,} – {max_c:,}")

else:
    st.info("📤 Upload a report to start analysis.")
