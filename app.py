# app.py
# -------------------------------------------------------------------
# Streamlit App: Medical Report Assistant (Pure-Python Cloud Version)
# - Handles PDF/DOCX/TXT
# - Extracts text with PyPDF or python-docx
# - Rule-based triage (no OCR, no spaCy)
# -------------------------------------------------------------------

import os, pathlib, re, yaml
from typing import Dict, Any, List, Tuple
from pypdf import PdfReader
from docx import Document
import streamlit as st
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm

# --- Streamlit config (must be first)
st.set_page_config(page_title="Medical Report Assistant", layout="wide")

ROOT = pathlib.Path(__file__).resolve().parent

# ---------------------------
# Default Rules
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
# Helpers
# ---------------------------
def load_rules() -> Dict[str, Any]:
    path = ROOT / "rules.yaml"
    if path.exists():
        return yaml.safe_load(open(path, "r", encoding="utf-8"))
    return _DEFAULT_RULES


def extract_text(file_path: str) -> str:
    """Extract text from PDF or DOCX"""
    if file_path.lower().endswith(".pdf"):
        text = []
        try:
            reader = PdfReader(file_path)
            for page in reader.pages:
                t = page.extract_text() or ""
                if t.strip():
                    text.append(t)
        except Exception as e:
            st.error(f"PDF reading failed: {e}")
        return "\n".join(text)
    elif file_path.lower().endswith(".docx"):
        doc = Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    elif file_path.lower().endswith(".txt"):
        return open(file_path, "r", encoding="utf-8", errors="ignore").read()
    else:
        return ""


def extract_entities_rulebased(text: str, rules: Dict[str, Any]) -> List[str]:
    entities = []
    for dis in rules.get("diseases", []):
        for kw in dis.get("keywords", []):
            if re.search(r"\b" + re.escape(kw) + r"\b", text, re.IGNORECASE):
                entities.append(kw)
    return list(set(entities))


def _match_condition(text: str, rules: Dict[str, Any]) -> Dict[str, Any]:
    low = text.lower()
    for dis in rules.get("diseases", []):
        for kw in dis.get("keywords", []):
            if kw.lower() in low:
                return dis
    return {}


def _severity_for(dis: Dict[str, Any], text: str, rules: Dict[str, Any]) -> Tuple[str, float, List[str]]:
    red_flags = []
    for rf in rules["general_rules"]["red_flags"]:
        if re.search(rf, text, re.IGNORECASE):
            red_flags.append(rf)
    if dis:
        for rf in dis.get("severity_rules", {}).get("red_flags", []):
            if re.search(rf, text, re.IGNORECASE):
                red_flags.append(rf)
    if red_flags:
        return "red", 0.9, red_flags
    elif dis:
        return "amber", 0.6, []
    else:
        return "green", 0.3, []


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


# ---------------------------
# Streamlit UI
# ---------------------------
st.title("🩺 Medical Report Assistant — Pure Python Cloud Version")
st.caption("Works 100% on Streamlit Cloud (no system binaries).")

with st.sidebar:
    st.header("⚙️ Settings")
    city = st.text_input("City", "Chennai")
    tier = st.selectbox("Hospital Tier", ["1", "2", "3"], index=1)
    out_name = st.text_input("Summary PDF name", "summary.pdf")

uploaded = st.file_uploader("📂 Upload report", type=["pdf", "docx", "txt"])

if uploaded:
    tmp_path = f"./_tmp_{uploaded.name}"
    with open(tmp_path, "wb") as f:
        f.write(uploaded.getbuffer())

    if st.button("🔍 Analyze"):
        rules = load_rules()
        text = extract_text(tmp_path)
        dis = _match_condition(text, rules)
        band, score, red_flags = _severity_for(dis, text, rules)
        entities = extract_entities_rulebased(text, rules)
        min_c, max_c = (0, 0)
        if dis:
            min_c, max_c = tuple(dis.get("cost_band", {}).get(f"tier_{tier}", [0, 0]))

        st.subheader("📊 Results")
        st.write(f"**Detected Disease:** {dis.get('name', 'Unknown')}")
        st.write(f"**Severity:** {band} (score {score:.2f})")
        st.write(f"**Entities Found:** {', '.join(entities) or '—'}")
        st.write(f"**Red Flags:** {', '.join(red_flags) or 'None'}")
        st.write(f"**Cost Estimate (₹):** {min_c} - {max_c}")

        out_pdf = _text_to_pdf(text[:1500], out_name)
        with open(out_pdf, "rb") as f:
            st.download_button("⬇️ Download Summary", f, file_name=out_name, mime="application/pdf")
else:
    st.info("📤 Upload a report to begin.")
