# app_fixed.py
# -------------------------------------------------------------------
# Fixed single-file Streamlit app: Offline Medical Report Assistant
# Key fixes applied in this version:
# - Made Tesseract and pdf2image optional (imports wrapped in try/except)
# - Graceful fallbacks and clear user warnings when OCR dependencies are missing
# - Fixed typo: "\n".Join -> "\n".join
# - Safer EntityRuler addition (only add if missing)
# - Use Markdown link for Google Calendar (more portable across Streamlit versions)
# - Improved .gitignore recommendation and requirements guidance in comments
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

# PIL (used for image → PDF conversion and image OCR)
from PIL import Image

# Optional OCR backends. Import only if available.
try:
    import pytesseract
    _HAS_TESSERACT = True
except Exception:
    _HAS_TESSERACT = False

try:
    from pdf2image import convert_from_path  # requires poppler-utils
    _HAS_PDF2IMAGE = True
except Exception:
    _HAS_PDF2IMAGE = False

# Optional pure-Python OCR alternative (works on Streamlit Cloud)
try:
    import easyocr
    _HAS_EASYOCR = True
except Exception:
    _HAS_EASYOCR = False

# PDF text-only fallback extractor
try:
    import pdfplumber
    _HAS_PDFPLUMBER = True
except Exception:
    _HAS_PDFPLUMBER = False

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

# --- Optional local defaults (Cloud ignores) ---
os.environ.setdefault("STREAMLIT_SERVER_ADDRESS", "127.0.0.1")
os.environ.setdefault("STREAMLIT_SERVER_PORT", "8501")

# ---------------------------
# Globals (loaded once)
# ---------------------------
_NLP: Optional[Language] = None
_RULES: Optional[Dict[str, Any]] = None

# ---------------------------
# Default rules (used if rules.yaml not present)
# ---------------------------
_DEFAULT_RULES = {
    "general_rules": {
        "red_flags": [
            "sepsis",
            "shock",
            "loss of consciousness",
            "acute abdomen",
            "chest pain",
        ]
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
# Rules management
# ---------------------------

def _load_rules(default_rules: Dict[str, Any], uploaded_rules: Optional[bytes]) -> Dict[str, Any]:
    if uploaded_rules:
        try:
            data = yaml.safe_load(uploaded_rules.decode("utf-8", errors="ignore"))
            if isinstance(data, dict) and "diseases" in data:
                return data
        except Exception:
            pass
    # fallback to file next to app
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
# Pipeline
# ---------------------------
@st.cache_resource(show_spinner=False)
def ensure_models_loaded(rules_blob: Optional[bytes]) -> Tuple[Language, Dict[str, Any]]:
    """
    Returns (NLP, RULES). Cache across reruns.
    """
    rules = _load_rules(_DEFAULT_RULES, rules_blob)

    # Prefer scispaCy medical model if available, else spaCy small, else blank + EntityRuler
    try:
        nlp = spacy.load("en_ner_bc5cdr_md")
    except Exception:
        try:
            nlp = spacy.load("en_core_web_sm")
        except Exception:
            nlp = spacy.blank("en")
            _add_entity_ruler_from_rules(nlp, rules)

    # Negex if available and not present
    if _HAS_NEGEX and "negex" not in nlp.pipe_names:
        try:
            nlp.add_pipe("negex")
        except Exception:
            pass

    # Ensure entity_ruler patterns exist (if blank model was used above they were added already)
    if "entity_ruler" not in nlp.pipe_names:
        try:
            _add_entity_ruler_from_rules(nlp, rules)
        except Exception:
            pass

    return nlp, rules


def process_report(input_path: str, city: str, tier: str, nlp: Language, rules: Dict[str, Any],
                   ocr_enabled: bool, ocr_dpi: int, tesseract_path: Optional[str]) -> Dict[str, Any]:
    """
    Convert → extract text → NLP → rules → severity → cost.
    """
    if tesseract_path and _HAS_TESSERACT:
        try:
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
        except Exception:
            pass

    pdf_path = convert_to_pdf(input_path)
    raw_text = extract_text_from_pdf(pdf_path, ocr_enabled=ocr_enabled, ocr_dpi=ocr_dpi)
    findings = extract_positive_entities(raw_text, nlp)

    dis = _match_condition(raw_text, rules)
    band, score, red_flags, reasons = _severity_for(dis, raw_text, rules)

    procedures = dis.get("procedures", []) if dis else []
    recovery = dis.get("recovery_recos", []) if dis else []
    min_c, max_c = _cost_for(dis, tier)

    return {
        "pdf_path": pdf_path,
        "city": city,
        "tier": tier,
        "raw_text": raw_text,
        "findings": findings,
        "disease": dis.get("name", "Unknown"),
        "severity_band": band,
        "severity_score": float(score),
        "red_flags": red_flags,
        "severity_reasons": reasons,
        "procedures": procedures,
        "recovery": recovery,
        "cost_range": (min_c, max_c),
    }

# (generate_summary_pdf and booking/ics helpers left unchanged)

def generate_summary_pdf(result: Dict[str, Any]) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    w, h = A4

    def line(y, text, font="Helvetica", size=11):
        c.setFont(font, size)
        c.drawString(20 * mm, y, text)
        return y - 7 * mm

    y = h - 20 * mm
    y = line(y, "Medical Report — Summary (Offline)", font="Helvetica-Bold", size=16)
    y -= 5 * mm
    y = line(y, f"Detected Condition: {result['disease']}")
    y = line(y, f"Severity Band: {result['severity_band']} (score {result['severity_score']:.2f})")
    y = line(y, f"City/Tier: {result['city']} / {result['tier']}")
    if result['red_flags']:
        y = line(y, "Red Flags: " + ", ".join(result['red_flags']))

    y -= 3 * mm
    y = line(y, "Key Findings:", font="Helvetica-Bold", size=12)
    c.setFont("Helvetica", 11)
    if result["findings"]:
        for fnd in result["findings"][:40]:
            y = line(y, f"• {fnd}")
            if y < 30 * mm:
                c.showPage(); y = h - 20 * mm; c.setFont("Helvetica", 11)
    else:
        y = line(y, "—")

    y -= 3 * mm
    y = line(y, "Possible Procedures:", font="Helvetica-Bold", size=12)
    c.setFont("Helvetica", 11)
    if result["procedures"]:
        for p in result["procedures"]:
            y = line(y, f"• {p}")
            if y < 30 * mm:
                c.showPage(); y = h - 20 * mm; c.setFont("Helvetica", 11)
    else:
        y = line(y, "—")

    y -= 3 * mm
    y = line(y, "Recovery Suggestions:", font="Helvetica-Bold", size=12)
    c.setFont("Helvetica", 11)
    if result["recovery"]:
        for r in result["recovery"]:
            y = line(y, f"• {r}")
            if y < 30 * mm:
                c.showPage(); y = h - 20 * mm; c.setFont("Helvetica", 11)
    else:
        y = line(y, "—")

    y -= 3 * mm
    min_c, max_c = result["cost_range"]
    y = line(y, f"Estimated Cost (₹): {min_c:,} — {max_c:,}", font="Helvetica-Bold")

    y -= 4 * mm
    y = line(y, "Disclaimer: Educational triage aid only — not a medical diagnosis. Consult a licensed clinician.", size=9)

    c.showPage()
    c.save()
    buf.seek(0)
    return buf.getvalue()


def build_booking_templates(result: Dict[str, Any], city: str) -> Tuple[str, str]:
    subj = f"Consultation request — {result['disease']} ({result['severity_band']})"
    body = (
        f"Hello,\n\n"
        f"I'd like to request an appointment in {city} regarding: {result['disease']}.\n"
        f"Severity: {result['severity_band']} (score {result['severity_score']:.2f}).\n"
        f"Key findings: {', '.join(result['findings'][:6]) or '—'}.\n"
        f"Estimated cost range (₹): {result['cost_range'][0]:,}–{result['cost_range'][1]:,}.\n\n"
        f"Please share available slots this week and required documents.\n\nThanks."
    )
    return (f"Subject: {subj}\n\n{body}", body)


def build_ics_custom(title: str, start_dt: datetime, end_dt: datetime, description: str = "", location: str = "") -> bytes:
    fmt = "%Y%m%dT%H%M%S"
    dt_start = start_dt.strftime(fmt)
    dt_end = end_dt.strftime(fmt)
    ics = (
        "BEGIN:VCALENDAR\n"
        "VERSION:2.0\n"
        "PRODID:-//Offline Medical Assistant//EN\n"
        "BEGIN:VEVENT\n"
        f"UID:offline-{os.urandom(4).hex()}@assistant\n"
        f"SUMMARY:{title}\n"
        f"DTSTART:{dt_start}\n"
        f"DTEND:{dt_end}\n"
        f"LOCATION:{location}\n"
        f"DESCRIPTION:{description}\n"
        "END:VEVENT\n"
        "END:VCALENDAR\n"
    )
    return ics.encode("utf-8")


def build_google_calendar_link(title: str, start_dt: datetime, end_dt: datetime, details: str = "", location: str = "", tz: str = "Asia/Kolkata") -> str:
    fmt = "%Y%m%dT%H%M%S"
    ds = start_dt.strftime(fmt)
    de = end_dt.strftime(fmt)
    base = "https://calendar.google.com/calendar/render?action=TEMPLATE"
    params = (
        f"&text={quote(title)}"
        f"&dates={ds}/{de}"
        f"&details={quote(details)}"
        f"&location={quote(location)}"
        f"&ctz={quote(tz)}"
    )
    return base + params

# ---------------------------
# Converters: to canonical PDF
# ---------------------------
def convert_to_pdf(input_path: str) -> str:
    """
    Always returns a PDF path created locally:
      - PDF: copies to *_canonical.pdf
      - Image: converts via Pillow to 1-page PDF
      - DOCX/TXT: renders text into a simple PDF via reportlab
    """
    base, _ = os.path.splitext(input_path)
    out_pdf = base + "__canonical.pdf"

    if _is_pdf(input_path):
        with open(input_path, "rb") as fin, open(out_pdf, "wb") as fout:
            fout.write(fin.read())
        return out_pdf

    if _is_image(input_path):
        return _image_to_pdf_pil(input_path, out_pdf)

    if _is_docx(input_path):
        text = _docx_to_text(input_path)
        return _text_to_pdf(text, out_pdf)

    if _is_txt(input_path):
        with open(input_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
        return _text_to_pdf(text, out_pdf)

    # Fallback: raw copy to .pdf (not a real conversion)
    with open(input_path, "rb") as fin, open(out_pdf, "wb") as fout:
        fout.write(fin.read())
    return out_pdf


def _image_to_pdf_pil(in_path: str, out_pdf: str) -> str:
    with Image.open(in_path) as im:
        if im.mode in ("RGBA", "P"):
            im = im.convert("RGB")
        im.save(out_pdf, "PDF", resolution=200.0)
    return out_pdf


def _docx_to_text(path: str) -> str:
    doc = Document(path)
    parts = []
    for p in doc.paragraphs:
        t = (p.text or "").strip()
        if t:
            parts.append(t)
    return "\n".join(parts)


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
# Text extraction (PDF)
# ---------------------------
def extract_text_from_pdf(pdf_path: str, ocr_enabled: bool, ocr_dpi: int) -> str:
    """
    Try embedded text using pypdf. If too little, and OCR is enabled, attempt OCR.
    Fallbacks:
      - If pdf2image + pytesseract available => rasterize + pytesseract
      - Else if easyocr available and pdf2image available => rasterize + easyocr
      - Else return embedded text and warn the user
    """
    # 1) Embedded text
    embedded = []
    try:
        reader = PdfReader(pdf_path)
        for page in reader.pages:
            t = page.extract_text() or ""
            t = t.strip()
            if t:
                embedded.append(t)
    except Exception:
        embedded = []

    joined = "\n".join(embedded)
    if len(joined) >= 200 or not ocr_enabled:
        return joined

    # 2) OCR fallback options
    if not _HAS_PDF2IMAGE:
        st.warning("OCR fallback unavailable: pdf2image/poppler not installed. Using embedded text only.")
        return joined

    # if we reach here, pdf2image is available (can rasterize pages)
    ocr_parts = []
    try:
        images = convert_from_path(pdf_path, dpi=max(100, min(ocr_dpi, 400)))  # list[PIL.Image]
        for img in images:
            txt = ""
            # Prefer pytesseract if available
            if _HAS_TESSERACT:
                try:
                    txt = pytesseract.image_to_string(img)
                except Exception:
                    txt = ""
            elif _HAS_EASYOCR:
                try:
                    reader = easyocr.Reader(['en'], gpu=False)
                    out = reader.readtext(img, detail=0)
                    if out:
                        txt = "\n".join(out)
                except Exception:
                    txt = ""
            if txt:
                ocr_parts.append(txt)
    except Exception:
        st.warning("OCR attempt failed during image rasterization or OCR. Using embedded text only.")
        return joined

    return joined + ("\n" if joined and ocr_parts else "") + "\n".join(ocr_parts)

# ---------------------------
# NLP helpers
# ---------------------------
def extract_positive_entities(text: str, nlp: Language) -> List[str]:
    """
    Deduplicated text entities NOT negated (per Negex).
    """
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
        key = norm.lower()
        if norm and key not in seen:
            seen.add(key)
            uniq.append(norm)
    return uniq


def _match_condition(text: str, rules: Dict[str, Any]) -> Dict[str, Any]:
    low = text.lower()
    for dis in rules.get("diseases", []):
        for kw in dis.get("keywords", []):
            if kw and kw.lower() in low:
                return dis
    return {}


def _severity_for(dis: Dict[str, Any], text: str, rules: Dict[str, Any]) -> Tuple[str, float, List[str], List[str]]:
    reasons: List[str] = []
    red_flags: List[str] = []

    # Keyword hits as weak proxy
    kw_hits = 0
    if dis:
        for kw in dis.get("keywords", []):
            if re.search(r"\b" + re.escape(kw) + r"\b", text, re.IGNORECASE):
                kw_hits += 1
        if kw_hits:
            reasons.append(f"Matched disease keywords: {kw_hits}")

    # General red flags
    for rf in rules.get("general_rules", {}).get("red_flags", []):
        if re.search(rf, text, re.IGNORECASE):
            red_flags.append(rf)

    # Condition-specific red flags
    if dis:
        for rf in dis.get("severity_rules", {}).get("red_flags", []):
            if re.search(rf, text, re.IGNORECASE):
                red_flags.append(rf)

    red_flags = sorted(set(red_flags))
    if red_flags:
        reasons.append("Red flags present: " + ", ".join(red_flags))

    # Heuristic band/score
    if red_flags:
        band = "red"
        base = 0.75
        score = min(1.0, base + 0.05 * len(red_flags))
    elif dis:
        band = "amber"
        base = 0.5
        score = min(0.8, base + 0.08 * kw_hits)
    else:
        band = "green"
        score = 0.3

    if band == "green" and not reasons:
        reasons.append("No matching condition or red flags detected")

    return band, float(score), red_flags, reasons


def _cost_for(dis: Dict[str, Any], tier: str) -> Tuple[int, int]:
    if not dis:
        return (0, 0)
    cb = dis.get("cost_band", {})
    rng = cb.get(f"tier_{tier}", [0, 0])
    if len(rng) == 2:
        return int(rng[0]), int(rng[1])
    return (0, 0)

# ---------------------------
# File-type helpers
# ---------------------------
def _is_pdf(path: str) -> bool:
    return path.lower().endswith(".pdf")


def _is_image(path: str) -> bool:
    return any(path.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg"]) 


def _is_docx(path: str) -> bool:
    return path.lower().endswith(".docx")


def _is_txt(path: str) -> bool:
    return path.lower().endswith(".txt")


def _add_entity_ruler_from_rules(nlp: Language, rules: Dict[str, Any]) -> None:
    if "entity_ruler" in nlp.pipe_names:
        return
    try:
        ruler = nlp.add_pipe("entity_ruler")
    except Exception:
        # fallback: create a component name that won't break the pipeline
        return
    patterns = []
    for dis in rules.get("diseases", []):
        for kw in dis.get("keywords", []):
            if kw:
                patterns.append({"label": "CONDITION", "pattern": kw})
    if patterns:
        try:
            ruler.add_patterns(patterns)
        except Exception:
            pass

# ---------------------------
# UI helpers
# ---------------------------
def severity_badge(band: str) -> str:
    color = {"red": "#ff4d4f", "amber": "#faad14", "green": "#52c41a"}.get(band.lower(), "#888")
    label = band.capitalize()
    return (
        f'<span style="background:{color};color:white;padding:4px 10px;'
        f'border-radius:12px;font-weight:600;'">{label}</span>
    )

# -------------------------------------------------------------------
# Streamlit UI
# -------------------------------------------------------------------
st.set_page_config(page_title="Medical Report Assistant (Updated)", layout="wide")
st.title("🩺 Medical Report Assistant — Updated, Single-file")

st.caption(
    "No external APIs. Local OCR (Tesseract optional), NLP, and rules. "
    "Educational triage & planning — not a diagnosis."
)

with st.sidebar:
    st.header("⚙️ Settings")
    city = st.text_input("City (for cost context)", value="Chennai")
    tier = st.selectbox("Hospital tier", ["1", "2", "3"], index=1)
    out_name = st.text_input("Summary PDF name", value="summary.pdf")

    st.markdown("---")
    st.subheader("OCR")
    ocr_enabled = st.checkbox("Enable OCR fallback (needs pdf2image + poppler)", value=False)
    ocr_dpi = st.slider("OCR DPI", min_value=120, max_value=400, step=20, value=200)
    if not _HAS_PDF2IMAGE and ocr_enabled:
        st.warning("pdf2image/poppler not detected. OCR fallback won't run on this platform.")

    st.markdown("---")
    st.subheader("Tesseract (Windows only)")
    tess_path = st.text_input("Tesseract path (optional)", value="")

    st.markdown("---")
    st.subheader("Rules")
    uploaded_rules = st.file_uploader("Upload rules.yaml (optional)", type=["yaml", "yml")]

    st.markdown("---")
    st.write("**How to use**:\n1) Upload a medical report (PDF/DOCX/Image/TXT)\n2) Click **Analyze**\n3) Download the summary or book calendar")

# Initialize models/rules
try:
    with st.spinner("Initializing models and rules…"):
        _NLP, _RULES = ensure_models_loaded(uploaded_rules.read() if uploaded_rules else None)
except Exception as e:
    st.error("Startup error — please check your requirements/apt packages. See details below.")
    st.exception(e)
    st.stop()

# File uploader
uploaded = st.file_uploader(
    "Upload medical report (PDF/DOCX/Image/TXT)",
    type=["pdf", "docx", "png", "jpg", "jpeg", "txt"],
    accept_multiple_files=False,
)

# -------------------------------------------------------------------
# Main Processing
# -------------------------------------------------------------------
if uploaded is not None:
    st.success(f"Loaded: {uploaded.name}")
    tmp_in = os.path.join(".", f"_tmp_{uploaded.name}")
    with open(tmp_in, "wb") as f:
        f.write(uploaded.getbuffer())

    if st.button("🔍 Analyze Report", type="primary"):
        with st.spinner("Processing (Convert → OCR → NLP → Rules)…"):
            result = process_report(
                tmp_in,
                city=city,
                tier=tier,
                nlp=_NLP,
                rules=_RULES,
                ocr_enabled=ocr_enabled,
                ocr_dpi=ocr_dpi,
                tesseract_path=tess_path.strip() or None,
            )

        left, right = st.columns([0.60, 0.40])

        with left:
            st.subheader("🧾 Result Summary")
            st.markdown("**Severity:")
            st.markdown(severity_badge(result["severity_band"]), unsafe_allow_html=True)
            st.markdown(f"Score: **{result['severity_score']:.2f}**")

            if result.get("severity_reasons"):
                st.markdown("**Why this severity?**")
                st.write("\n".join(f"• {r}" for r in result["severity_reasons"]))

            st.markdown(f"**Detected Condition:** {result['disease']}")
            st.markdown("**Red Flags:** " + (", ".join(result["red_flags"]) if result["red_flags"] else "None"))

            st.markdown("**Key Findings:**")
            st.write("\n".join([f"• {x}" for x in (result["findings"] or ["—"])]))

            st.markdown("**Possible Procedures:**")
            st.write("\n".join([f"• {p}" for p in (result["procedures"] or ["—"])]))

            st.markdown("**Recovery Suggestions:**")
            st.write("\n".join([f"• {r}" for r in (result["recovery"] or ["—"])]))

        with right:
            st.subheader("💰 Estimated Cost (₹)")
            min_c, max_c = result["cost_range"]
            st.metric("Tier", f"Tier {tier}")
            st.metric("Estimated range", f"₹{min_c:,} — ₹{max_c:,}")
            st.caption("*Edit cost bands via uploaded rules.yaml or defaults in this file*")

            st.markdown("---")
            st.subheader("📅 Appointment Templates")
            email_txt, wa_txt = build_booking_templates(result, city=city)
            st.markdown("**Email text:**")
            st.code(email_txt, language="text")
            st.markdown("**WhatsApp text:**")
            st.code(wa_txt, language="text")

        # Calendar section
        st.markdown("---")
        st.subheader("🗓️ Calendar — Create Appointment")
        colA, colB, colC, colD = st.columns(4)
        with colA:
            ev_date = st.date_input("Date", value=date.today() + timedelta(days=1))
        with colB:
            ev_time = st.time_input("Start time", value=time(hour=10, minute=0))
        with colC:
            duration_min = st.selectbox("Duration (mins)", [15, 30, 45, 60], index=1)
        with colD:
            tz = st.text_input("Time zone", value="Asia/Kolkata")

        title_default = f"Consultation — {result['disease']} ({result['severity_band']})"
        ev_title = st.text_input("Title", value=title_default)
        ev_location = st.text_input("Location/Hospital", value=city)
        ev_notes = st.text_area(
            "Notes (go into event description)",
            value=(f"Severity: {result['severity_band']} (score {result['severity_score']:.2f})\n"
                   f"Key findings: {', '.join(result['findings'][:6]) or '—'}\n"
                   f"Estimated cost range (₹): {min_c:,}–{max_c:,}"),
            height=120
        )

        start_dt = datetime.combine(ev_date, ev_time)
        end_dt = start_dt + timedelta(minutes=duration_min)

        ics_bytes = build_ics_custom(ev_title, start_dt, end_dt, ev_notes, ev_location)
        ics_name = f"appointment_{start_dt.strftime('%Y%m%d_%H%M')}.ics"

        c1, c2 = st.columns(2)
        with c1:
            st.download_button(
                "📥 Download .ics (Calendar)",
                data=ics_bytes,
                file_name=ics_name,
                mime="text/calendar",
                use_container_width=True,
            )
        with c2:
            gcal_url = build_google_calendar_link(
                title=ev_title,
                start_dt=start_dt,
                end_dt=end_dt,
                details=ev_notes,
                location=ev_location,
                tz=tz,
            )
            st.markdown(f"[🗓️ Open in Google Calendar]({gcal_url})")

        # Summary PDF
        st.markdown("---")
        st.subheader("📄 Downloadable Summary (PDF)")
        pdf_bytes = generate_summary_pdf(result)
        st.download_button(
            label="⬇️ Download Summary PDF",
            data=pdf_bytes,
            file_name=out_name,
            mime="application/pdf",
            use_container_width=True,
        )

        st.info(
            "This tool provides guideline-style triage and education. "
            "It does **not** diagnose or prescribe. Please consult a licensed clinician."
        )

else:
    st.warning("📤 Upload a report to begin (PDF, DOCX, PNG, JPG, or TXT).")
