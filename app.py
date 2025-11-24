# app.py
# Clinical Report Helper — Safe PDF handling + Extracted text tables (Key-Value & Line table)
# Default rules.yaml path set to the path you provided earlier.

import streamlit as st
import io, re, os, json, uuid
from datetime import datetime, date, time, timedelta
from typing import Dict, List, Any, Tuple
from PIL import Image
import pdfplumber
import docx2txt

# PDF library
from fpdf import FPDF

# Optional OCR
try:
    import pytesseract
    OCR_AVAILABLE = True
except Exception:
    pytesseract = None
    OCR_AVAILABLE = False

# Optional YAML
try:
    import yaml
    YAML_AVAILABLE = True
except Exception:
    yaml = None
    YAML_AVAILABLE = False

# Change this path if your rules.yaml is elsewhere
RULES_FILE = r"D:\med_internship\rules.yaml"
BOOKINGS_FILE = "bookings.json"

# ---------------------------
# Streamlit config (first call)
# ---------------------------
st.set_page_config(page_title="Clinical Report Helper", page_icon="🩺", layout="wide")

# ---------------------------
# Small CSS
# ---------------------------
st.markdown(
    """
    <style>
    .small-muted {color:#6b7280;font-size:12px;}
    .card {border:1px solid #e5e7eb;border-radius:12px;padding:14px;margin-top:8px;background:#fff;}
    .section-title {font-weight:600;font-size:18px;margin-top:8px;margin-bottom:0px;}
    .hr {height:1px;background:#e5e7eb;border:none;margin:16px 0;}
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------
# Session defaults
# ---------------------------
defaults = {
    "extracted_text": "",
    "entities": {},
    "problems": [],
    "results": [],
    "latest_pdf_bytes": b"",
    "latest_ics_bytes": b"",
    "receipt_pdf_bytes": b"",
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# ---------------------------
# Safe text cleaner for PDF (avoid unicode issues with FPDF)
# ---------------------------
def clean_text_for_pdf(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    table = {
        "–": "-", "—": "-", "“": '"', "”": '"', "‘": "'", "’": "'",
        "•": "-", "…": "...", "₹": "Rs ", "\u00a0": " ",
        "°": " deg", "±": "+/-"
    }
    for k, v in table.items():
        s = s.replace(k, v)
    # remove other non-ascii
    s = s.encode("ascii", "replace").decode("ascii")
    return s

def clean_text(s: str) -> str:
    # preserve unicode for display, but keep it safe (strip excessive control chars)
    if s is None:
        return ""
    s = str(s)
    s = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", s)
    return s

# ---------------------------
# PDF helpers
# ---------------------------
def get_pdf_bytes(pdf_obj: FPDF) -> bytes:
    out = pdf_obj.output(dest="S")
    if isinstance(out, bytes):
        return out
    return out.encode("latin-1")

def set_pdf_font(pdf: FPDF, size=12, style=""):
    try:
        pdf.set_font("Arial", style=style, size=size)
    except Exception:
        try:
            pdf.set_font("Helvetica", style=style, size=size)
        except Exception:
            pdf.set_font(size=size)

def usable_pdf_width(pdf: FPDF) -> float:
    try:
        return pdf.w - pdf.l_margin - pdf.r_margin
    except Exception:
        return 180.0

# ---------------------------
# YAML loader & KB builder
# ---------------------------
def load_rules_yaml(path: str) -> Dict[str, Any]:
    if not YAML_AVAILABLE:
        return {}
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data or {}
    except Exception:
        return {}

def build_kb(rules: Dict[str, Any]) -> Dict[str, Any]:
    kb = {"city_cost_modifiers": {}, "hospitals": {}, "conditions": {}, "general_red_flags": []}
    kb["city_cost_modifiers"] = rules.get("city_cost_modifiers", {}) or {}
    kb["hospitals"] = rules.get("hospitals", {}) or {}
    diseases = rules.get("diseases", []) or []
    for d in diseases:
        name = d.get("name", "unknown")
        key = re.sub(r"\s+", "_", name.strip().lower())
        kb["conditions"][key] = d
    kb["general_red_flags"] = rules.get("general_rules", {}).get("red_flags", []) or []
    return kb

RULES = load_rules_yaml(RULES_FILE)
KB = build_kb(RULES)

if not YAML_AVAILABLE:
    st.warning("PyYAML not installed — rules.yaml won't be loaded. Install PyYAML to enable the disease/hospital KB.")
elif not RULES:
    st.info(f"No rules.yaml found at {RULES_FILE} — app will still run with limited features.")

# ---------------------------
# Extraction functions
# ---------------------------
def extract_text_from_file(uploaded) -> Tuple[str, List[str]]:
    warns = []
    name = uploaded.name.lower()
    data = uploaded.read()
    # PDF
    if name.endswith(".pdf"):
        try:
            text = ""
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for p in pdf.pages:
                    text += "\n" + (p.extract_text() or "")
            return text.strip(), warns
        except Exception as e:
            return "", [f"PDF extraction error: {e}"]
    # DOCX
    if name.endswith(".docx"):
        try:
            buf = io.BytesIO(data)
            return docx2txt.process(buf) or "", warns
        except Exception as e:
            return "", [f"DOCX extraction error: {e}"]
    # Image
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        if OCR_AVAILABLE:
            txt = pytesseract.image_to_string(im) or ""
            return txt, warns
        else:
            warns.append("OCR not available. Install Tesseract+pytesseract for image OCR.")
            return "", warns
    except Exception:
        return "", ["Unsupported file. Upload PDF/DOCX/JPG/PNG."]

# ---------------------------
# Parsing & detection
# ---------------------------
def parse_entities(text: str) -> Dict[str, str]:
    ents = {}
    ents["Name"] = ""
    ents["Age"] = ""
    ents["Sex"] = ""
    m = re.search(r"(?i)\b(patient\s*name|name)\s*[:\-]\s*([A-Za-z ,.'-]{2,80})", text or "")
    if m: ents["Name"] = m.group(2).strip()
    m = re.search(r"(?i)\bage\s*[:\-]\s*(\d{1,3})", text or "")
    if m: ents["Age"] = m.group(1)
    m = re.search(r"(?i)\b(sex|gender)\s*[:\-]\s*(male|female|m|f|other)", text or "")
    if m: ents["Sex"] = m.group(2).capitalize()
    return ents

def summarize_problems(text: str) -> List[str]:
    probs = []
    pats = [r"(?i)impression\s*[:\-]\s*(.+)", r"(?i)diagnosis\s*[:\-]\s*(.+)", r"(?i)conclusion\s*[:\-]\s*(.+)", r"(?i)findings\s*[:\-]\s*(.+)"]
    for pat in pats:
        for m in re.finditer(pat, text or ""):
            s = m.group(1).strip()
            if s and s not in probs:
                probs.append(s[:400])
    if not probs:
        lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
        keywords = ["pain","lesion","fracture","mass","infection","infarct","hernia","stone","blockage","tumor","angina","colic"]
        guesses = [l for l in lines if any(w in l.lower() for w in keywords)]
        probs = list(dict.fromkeys(guesses[:5]))
    return probs

def matches_keyword(text: str, keyword: str) -> bool:
    if not keyword or len(keyword.strip()) < 2:
        return False
    return bool(re.search(rf"\b{re.escape(keyword.strip().lower())}\b", (text or "").lower()))

def detect_conditions(text: str) -> List[Dict[str,Any]]:
    results = []
    t = (text or "").lower()
    for key, meta in KB.get("conditions", {}).items():
        kws = meta.get("keywords", []) or []
        hits = [kw for kw in kws if matches_keyword(t, kw)]
        if hits:
            results.append({
                "key": key,
                "name": meta.get("name") or meta.get("display") or key,
                "hits": hits,
                "procedures": meta.get("procedures", []),
                "recovery_recos": meta.get("recovery_recos", []),
                "treatment_span": meta.get("treatment_span", ""),
                "diet_plan": meta.get("diet_plan", []),
                "lifestyle_plan": meta.get("lifestyle_plan", []),
                "post_surgery_plan": meta.get("post_surgery_plan", []),
                "cost_inr": meta.get("cost_inr", [0,0])
            })
    results.sort(key=lambda r: len(r.get("hits",[])), reverse=True)
    return results

def severity_score(text: str, cond: Dict[str,Any]) -> int:
    t = (text or "").lower()
    score = 0
    for rf in KB.get("general_red_flags", []):
        if rf.lower() in t:
            score += 10
    for rf in cond.get("severity_rules", {}).get("red_flags", []) if cond.get("severity_rules") else []:
        if rf.lower() in t:
            score += 12
    score += min(5, len(cond.get("hits", []))) * 4
    if any(x in t for x in ["normal study", "within normal limits", "no acute"]):
        score = min(score, 5)
    return max(0, min(score, 95))

def india_adjust_cost(base: List[int], city: str) -> Tuple[int,int]:
    if not base or len(base) != 2: return (0,0)
    m = KB.get("city_cost_modifiers", {}).get((city or "").strip().lower(), KB.get("city_cost_modifiers", {}).get("default", 1.0))
    try:
        return (int(base[0]*float(m)), int(base[1]*float(m)))
    except Exception:
        return (base[0], base[1])

# ---------------------------
# UI — Upload
# ---------------------------
st.title("🩺 Clinical Report Helper")
st.write('<span class="small-muted">For education/information only. Not medical advice.</span>', unsafe_allow_html=True)
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)

uploaded = st.file_uploader("Upload PDF / DOCX / JPG / PNG", type=["pdf","docx","jpg","jpeg","png"])
if not uploaded:
    st.stop()

text, warns = extract_text_from_file(uploaded)
if warns:
    st.warning("\n".join(warns))

st.session_state["extracted_text"] = text

# ---------------------------
# Show extracted text tables
# ---------------------------
st.header("Extracted Text — Key & Line Tables")

# Key–Value auto-detected fields (Option C)
ents = parse_entities(text)
probs = summarize_problems(text)
st.session_state["entities"] = ents
st.session_state["problems"] = probs

kv_rows = [
    ("Name", ents.get("Name","—")),
    ("Age", ents.get("Age","—")),
    ("Sex", ents.get("Sex","—")),
    ("Top Impression / Problems", "; ".join(probs) if probs else "—"),
]
import pandas as pd
df_kv = pd.DataFrame(kv_rows, columns=["Field","Value"])
st.subheader("Auto-detected key fields")
st.table(df_kv)

# Line-by-line table
lines = [l for l in (text or "").splitlines() if l.strip()]
if not lines:
    lines = [text or ""]
df_lines = pd.DataFrame({"Line": list(range(1, len(lines)+1)), "Text": lines})
st.subheader("Extracted text — line by line")
st.dataframe(df_lines, use_container_width=True)

# Problems / impressions as separate table
st.subheader("Problems / Impressions / Diagnoses (extracted)")
df_probs = pd.DataFrame({"Impression": probs or ["—"]})
st.table(df_probs)

# ---------------------------
# Condition detection & treatment display
# ---------------------------
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
st.header("Detected Conditions & Treatment (from rules.yaml)")

results = detect_conditions(text)
st.session_state["results"] = results

if not results:
    st.info("No conditions matched from rules.yaml (or rules.yaml not loaded).")
else:
    for r in results:
        score = severity_score(text, r)
        st.subheader(f"{r.get('name')}  — Severity {score}%")
        st.write("Matched keywords:", ", ".join(r.get("hits", [])))
        st.write("Procedures:", "; ".join(r.get("procedures", []) or ["—"]))
        st.write("Treatment duration / span:", r.get("treatment_span") or "Varies")
        if r.get("diet_plan"):
            st.write("Diet plan:")
            for item in r.get("diet_plan", []):
                st.write("-", item)
        if r.get("lifestyle_plan"):
            st.write("Lifestyle suggestions:")
            for item in r.get("lifestyle_plan", []):
                st.write("-", item)
        if r.get("post_surgery_plan"):
            st.write("Post-surgery / aftercare:")
            for item in r.get("post_surgery_plan", []):
                st.write("-", item)
        low, high = india_adjust_cost(r.get("cost_inr", [0,0]), city="bangalore")
        if low or high:
            st.success(f"Estimated cost (Bangalore): ₹{low:,} – ₹{high:,}")
        st.markdown("---")

# ---------------------------
# Appointment booking (confirm before generating files)
# ---------------------------
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
st.header("Appointment Booking & Files")

# City/hospital selection (defaults to Bangalore if available)
cities = sorted(list(KB.get("hospitals", {}).keys()))
default_city = "bangalore" if "bangalore" in cities else (cities[0] if cities else "")
city = st.selectbox("City (for hospital suggestions)", [""] + cities, index=(1 if default_city else 0))
if city:
    hospital_list = KB.get("hospitals", {}).get(city, KB.get("hospitals", {}).get("default", []))
else:
    hospital_list = KB.get("hospitals", {}).get("default", [])
hospital_names = [h.get("name","") for h in hospital_list]
chosen_hospital = st.selectbox("Choose hospital", [""] + hospital_names)
chosen_hospital_obj = next((h for h in hospital_list if h.get("name")==chosen_hospital), None)

# Department & doctor selection if present
doctor_options = ["Duty Doctor"]
if chosen_hospital_obj:
    depts = chosen_hospital_obj.get("departments", {})
    if isinstance(depts, dict) and depts:
        dept_choice = st.selectbox("Department", [""] + list(depts.keys()))
        if dept_choice:
            doctor_options = depts.get(dept_choice, doctor_options)
    else:
        # allow free doctor input
        manual_doc = st.text_input("Preferred Doctor (optional)", "")
        if manual_doc:
            doctor_options = [manual_doc]

doctor = st.selectbox("Doctor", doctor_options)

# Appointment date/time
appt_date = st.date_input("Appointment date", value=date.today(), min_value=date.today())
appt_time = st.time_input("Appointment time", value=time(10,0))

# Confirm checkbox
confirm = st.checkbox("I confirm these appointment details and want to generate files")
if not confirm:
    st.info("Confirm to enable file generation and downloads.")
else:
    st.write("Booking summary:")
    st.write({
        "City": city,
        "Hospital": chosen_hospital,
        "Doctor": doctor,
        "Date": str(appt_date),
        "Time": str(appt_time),
        "Condition": results[0].get("name") if results else ""
    })

    if st.button("Generate and Download Appointment PDF + ICS"):
        # PDF
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=12)
        pdf.add_page()
        set_pdf_font(pdf, size=16, style="B")
        width = usable_pdf_width(pdf)
        pdf.cell(0, 10, clean_text_for_pdf("Appointment Confirmation"), ln=1)
        set_pdf_font(pdf, size=11)
        pdf.ln(4)
        pdf.multi_cell(width, 7, clean_text_for_pdf(f"Patient: {ents.get('Name','')}"))
        pdf.multi_cell(width, 7, clean_text_for_pdf(f"City: {city}"))
        pdf.multi_cell(width, 7, clean_text_for_pdf(f"Hospital: {chosen_hospital}"))
        pdf.multi_cell(width, 7, clean_text_for_pdf(f"Doctor: {doctor}"))
        pdf.multi_cell(width, 7, clean_text_for_pdf(f"Condition: {results[0].get('name') if results else '—'}"))
        pdf.multi_cell(width, 7, clean_text_for_pdf(f"Date: {appt_date}  Time: {appt_time}"))
        pdf.ln(5)
        pdf.multi_cell(width, 7, clean_text_for_pdf("Please bring your previous reports and a valid ID."))

        pdf_bytes = get_pdf_bytes(pdf)
        st.session_state["latest_pdf_bytes"] = pdf_bytes

        # ICS
        dtstart = datetime.combine(appt_date, appt_time)
        dtend = dtstart + timedelta(minutes=30)
        ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Clinical Report Helper//EN
BEGIN:VEVENT
UID:{uuid.uuid4()}
DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}
DTSTART:{dtstart.strftime('%Y%m%dT%H%M%S')}
DTEND:{dtend.strftime('%Y%m%dT%H%M%S')}
SUMMARY:Appointment - {clean_text(results[0].get('name') if results else 'Appointment')}
DESCRIPTION:Doctor: {clean_text(doctor)}
LOCATION:{clean_text(chosen_hospital)}, {clean_text(city)}
END:VEVENT
END:VCALENDAR
"""
        ics_bytes = ics.encode("utf-8")
        st.session_state["latest_ics_bytes"] = ics_bytes

        st.success("Files generated — download below.")
        st.download_button("⬇️ Download Appointment PDF", data=pdf_bytes, file_name="appointment.pdf", mime="application/pdf")
        st.download_button("📅 Add to Calendar (.ics)", data=ics_bytes, file_name="appointment.ics", mime="text/calendar")

        # also build a small receipt PDF
        receipt_pdf = FPDF()
        receipt_pdf.add_page()
        set_pdf_font(receipt_pdf, size=14, style="B")
        receipt_pdf.cell(0, 10, clean_text_for_pdf("Booking Receipt"), ln=1)
        set_pdf_font(receipt_pdf, size=11)
        receipt_pdf.ln(4)
        rid = str(uuid.uuid4())[:8]
        receipt_pdf.multi_cell(0, 7, clean_text_for_pdf(f"Booking ID: {rid}"))
        receipt_pdf.multi_cell(0, 7, clean_text_for_pdf(f"Patient: {ents.get('Name','')}"))
        receipt_pdf.multi_cell(0, 7, clean_text_for_pdf(f"Hospital: {chosen_hospital}"))
        receipt_pdf.multi_cell(0, 7, clean_text_for_pdf(f"Doctor: {doctor}"))
        receipt_pdf.multi_cell(0, 7, clean_text_for_pdf(f"Date: {appt_date} Time: {appt_time}"))
        rbytes = get_pdf_bytes(receipt_pdf)
        st.session_state["receipt_pdf_bytes"] = rbytes
        st.download_button("⬇️ Download Booking Receipt (PDF)", data=rbytes, file_name=f"receipt_{rid}.pdf", mime="application/pdf")

# ---------------------------
# Save booking locally
# ---------------------------
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
st.header("My Bookings (local)")

def load_bookings():
    if not os.path.exists(BOOKINGS_FILE):
        return []
    try:
        with open(BOOKINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_booking(rec):
    rows = load_bookings()
    rows.append(rec)
    with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=2, ensure_ascii=False)

rows = load_bookings()
if rows:
    st.dataframe(rows)
else:
    st.info("No bookings saved yet.")

# quick save button if latest generated
if st.session_state.get("latest_pdf_bytes") and confirm and st.button("Save current booking locally"):
    rec = {
        "id": str(uuid.uuid4())[:8],
        "patient": ents.get("Name",""),
        "city": city,
        "hospital": chosen_hospital,
        "doctor": doctor,
        "date": str(appt_date),
        "time": str(appt_time),
        "saved_at": datetime.now().isoformat()
    }
    save_booking(rec)
    st.success(f"Saved booking {rec['id']}")

st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
st.caption("© 2025 — Educational only. Not medical advice.")
