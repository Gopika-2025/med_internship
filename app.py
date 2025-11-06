# app.py
# -------------------------------------------------------------------
# Streamlit App: Medical Report Assistant (with spaCy + Negex)
# - Extracts and analyzes text from PDF/DOCX/Image/TXT
# - NLP (spaCy) + Negex for medical condition detection
# - Rule-based triage scoring + PDF summary + optional model.pkl
# -------------------------------------------------------------------

# 1️⃣ Imports
import os, sys, pathlib, re, io
from typing import List, Tuple, Dict, Any, Optional
from datetime import datetime
import pickle
import streamlit as st

# ✅ Must be FIRST Streamlit command
st.set_page_config(page_title="Medical Report Assistant (NLP)", layout="wide")

# 2️⃣ Dependencies
import yaml
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import mm
from pypdf import PdfReader
from PIL import Image
import pytesseract

# OCR fallback
try:
    from pdf2image import convert_from_path
    _HAS_PDF2IMAGE = True
except Exception:
    _HAS_PDF2IMAGE = False

# 3️⃣ NLP Setup
try:
    import spacy
    from spacy.language import Language
    from negspacy.negation import Negex
    import warnings

    warnings.filterwarnings("ignore")

    # Auto-download model if not present
    try:
        spacy.load("en_core_web_sm")
    except OSError:
        os.system("python -m spacy download en_core_web_sm")

    _NLP = spacy.load("en_core_web_sm")

    # Add Negex if available
    if "negex" not in _NLP.pipe_names:
        _NLP.add_pipe("negex")

    _HAS_NLP = True
    st.sidebar.success("✅ spaCy + Negex loaded")
except Exception as e:
    _HAS_NLP = False
    _NLP = None
    st.sidebar.warning(f"⚠️ NLP not loaded: {e}")

# 4️⃣ Global Setup
ROOT = pathlib.Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_RULES: Optional[Dict[str, Any]] = None

# 5️⃣ Optional ML Model
MODEL = None
MODEL_PATH = ROOT / "model.pkl"
if MODEL_PATH.exists():
    try:
        with open(MODEL_PATH, "rb") as f:
            MODEL = pickle.load(f)
        st.sidebar.info("✅ Model loaded successfully")
    except Exception as e:
        st.sidebar.warning(f"⚠️ Could not load model.pkl: {e}")
else:
    st.sidebar.info("ℹ️ No model.pkl found — skipping ML prediction")

# 6️⃣ Default Rule Base
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

# 7️⃣ Helper Functions
def load_rules() -> Dict[str, Any]:
    rules_path = ROOT / "rules.yaml"
    if rules_path.exists():
        with open(rules_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return _DEFAULT_RULES


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
    ext = input_path.lower()
    if ext.endswith(".pdf"):
        with open(input_path, "rb") as fin, open(out_pdf, "wb") as fout:
            fout.write(fin.read())
        return out_pdf
    if ext.endswith((".png", ".jpg", ".jpeg")):
        with Image.open(input_path) as im:
            if im.mode in ("RGBA", "P"):
                im = im.convert("RGB")
            im.save(out_pdf, "PDF", resolution=200.0)
        return out_pdf
    if ext.endswith(".docx"):
        text = "\n".join([p.text for p in Document(input_path).paragraphs if p.text.strip()])
        return _text_to_pdf(text, out_pdf)
    if ext.endswith(".txt"):
        text = open(input_path, "r", encoding="utf-8", errors="ignore").read()
        return _text_to_pdf(text, out_pdf)
    return out_pdf


def extract_text_from_pdf(pdf_path: str) -> str:
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
    if not _HAS_NLP:
        return []
    doc = _NLP(text)
    entities = []
    for ent in doc.ents:
        neg = getattr(ent._, "negex", False)
        if not neg:
            entities.append(ent.text)
    return list(set(entities))


def _match_condition(text: str, rules: Dict[str, Any]) -> Dict[str, Any]:
    low = text.lower()
    for dis in rules.get("diseases", []):
        for kw in dis.get("keywords", []):
            if kw.lower() in low:
                return dis
    return {}


def _severity_for(dis: Dict[str, Any], text: str, rules: Dict[str, Any]) -> Tuple[str, float, List[str], List[str]]:
    reasons, red_flags = [], []
    if dis:
        for kw in dis.get("keywords", []):
            if re.search(r"\b" + re.escape(kw) + r"\b", text, re.IGNORECASE):
                reasons.append(f"Matched keyword: {kw}")
        for rf in rules["general_rules"]["red_flags"]:
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


def process_report(input_path: str, city: str, tier: str) -> Dict[str, Any]:
    rules = load_rules()
    pdf_path = convert_to_pdf(input_path)
    text = extract_text_from_pdf(pdf_path)
    entities = extract_entities(text)
    dis = _match_condition(text, rules)
    band, score, red_flags, reasons = _severity_for(dis, text, rules)
    procedures = dis.get("procedures", []) if dis else []
    recovery = dis.get("recovery_recos", []) if dis else []
    min_c, max_c = _cost_for(dis, tier)
    return {
        "pdf_path": pdf_path,
        "city": city,
        "tier": tier,
        "raw_text": text,
        "entities": entities,
        "disease": dis.get("name", "Unknown"),
        "severity_band": band,
        "severity_score": score,
        "red_flags": red_flags,
        "reasons": reasons,
        "procedures": procedures,
        "recovery": recovery,
        "cost_range": (min_c, max_c),
    }

# 8️⃣ Streamlit UI
st.title("🩺 Medical Report Assistant — NLP Mode")
st.caption("spaCy + Negex enabled version with OCR and PDF summary.")

with st.sidebar:
    st.header("⚙️ Settings")
    city = st.text_input("City", "Chennai")
    tier = st.selectbox("Hospital Tier", ["1", "2", "3"], index=1)
    out_name = st.text_input("Summary PDF name", "summary.pdf")

uploaded = st.file_uploader("📂 Upload Report", type=["pdf", "docx", "png", "jpg", "jpeg", "txt"])

if uploaded:
    st.success(f"✅ Loaded: {uploaded.name}")
    tmp_in = f"./_tmp_{uploaded.name}"
    with open(tmp_in, "wb") as f:
        f.write(uploaded.getbuffer())

    if st.button("🔍 Analyze Report"):
        with st.spinner("Processing..."):
            result = process_report(tmp_in, city, tier)

        st.subheader("🧾 Summary")
        st.write(f"**Detected Condition:** {result['disease']}")
        st.write(f"**Severity:** {result['severity_band']} (score {result['severity_score']:.2f})")
        st.write(f"**Entities:** {', '.join(result['entities']) or '—'}")
        st.write(f"**Red Flags:** {', '.join(result['red_flags']) or 'None'}")
        st.write(f"**Procedures:** {', '.join(result['procedures']) or '—'}")
        st.write(f"**Recovery:** {', '.join(result['recovery']) or '—'}")
        st.write(f"**Estimated Cost (₹):** {result['cost_range'][0]} — {result['cost_range'][1]}")

        if MODEL:
            try:
                X = [[len(result["raw_text"]) % 10, len(result["entities"])]]
                pred = MODEL.predict(X)
                st.info(f"🤖 Model Prediction: {pred[0]}")
            except Exception as e:
                st.warning(f"Model prediction failed: {e}")

        pdf_path = _text_to_pdf(result["raw_text"][:1000], out_name)
        with open(pdf_path, "rb") as f:
            st.download_button("⬇️ Download Summary", f, file_name=out_name, mime="application/pdf")

else:
    st.info("📤 Upload a report to begin.")
