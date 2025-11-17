# app.py
# -------------------------------------------------------------------
# Clinical Report Helper (YAML-driven)
# - Loads rules (diseases, general red flags, cities, hospitals) from rules.yaml
# - Upload PDF/DOCX/IMAGE -> extract text (OCR optional) -> detect conditions
# - Booking system uses hospitals from rules.yaml (stores bookings in bookings.json)
# - Builds Full Report PDF, Booking Receipt PDF, .ics; flexible SMTP email
# - Informational only — not medical advice.
# -------------------------------------------------------------------

import streamlit as st
import io, re, json, uuid, os
from typing import Dict, List, Tuple, Any
from datetime import datetime, date, time, timedelta

import pandas as pd
import pdfplumber
import docx2txt
from PIL import Image

# Safe optional OCR import
try:
    import pytesseract
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False

from fpdf import FPDF
import smtplib, ssl
from email.message import EmailMessage

# YAML loader
try:
    import yaml
    YAML_AVAILABLE = True
except Exception:
    yaml = None
    YAML_AVAILABLE = False

RULES_FILE = "rules.yaml"
BOOKINGS_FILE = "bookings.json"

# -----------------------------
# Page & styling
# -----------------------------
st.set_page_config(page_title="Clinical Report Helper (India)", page_icon="🩺", layout="wide")
st.markdown("""
<style>
.small-muted {color:#6b7280;font-size:12px;}
.card {border:1px solid #e5e7eb;border-radius:12px;padding:14px;margin-top:8px;}
.section-title {font-weight:600;font-size:18px;margin-top:8px;margin-bottom:0px;}
.hr {height:1px;background:#e5e7eb;border:none;margin:16px 0;}
.sev-tag {display:inline-block;border-radius:10px;padding:4px 10px;font-weight:700;}
</style>
""", unsafe_allow_html=True)

# -----------------------------
# Session defaults
# -----------------------------
defaults = {
    "extracted_text": "",
    "entities": {},
    "problems": [],
    "best_condition": None,
    "alt_conditions": [],
    "hospitals": [],
    "latest_pdf_bytes": b"",
    "latest_ics_bytes": b"",
    "receipt_pdf_bytes": b"",
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# -----------------------------
# Load rules.yaml
# -----------------------------
def load_rules(path: str) -> Dict[str, Any]:
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

RULES = load_rules(RULES_FILE)
if not YAML_AVAILABLE:
    st.error("PyYAML not installed. Install with `pip install PyYAML` and restart the app.")
if not RULES:
    st.warning(f"rules.yaml not found or empty at '{RULES_FILE}'. Some features (diseases/hospitals) will be unavailable.")

GENERAL_RULES = RULES.get("general_rules", {}) or {}
DISEASES = RULES.get("diseases", []) or []
# cities as dict of city -> {cost_modifier:, hospitals: [...]}
CITIES = RULES.get("cities", {}) or {}
HOSPITALS_ROOT = RULES.get("hospitals", {}) or {}

# Helper to normalize keys
def city_key(k: str) -> str:
    return (k or "").strip().lower()

# -----------------------------
# Utilities
# -----------------------------
def normalize_text(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip().lower()

def list_india_cities() -> List[str]:
    # prefer cities from CITIES keys, if absent, try hospitals root keys
    keys = []
    if CITIES:
        keys = sorted([k for k in CITIES.keys() if k and k != "default"])
    elif HOSPITALS_ROOT:
        keys = sorted([k for k in HOSPITALS_ROOT.keys() if k and k != "default"])
    return keys

def india_adjust_cost(base: List[int], city: str) -> Tuple[int, int]:
    if not base or len(base) != 2:
        return (0, 0)
    cm = 1.0
    ck = city_key(city)
    if CITIES and ck in CITIES:
        cm = float(CITIES[ck].get("cost_modifier", 1.0))
    elif HOSPITALS_ROOT and ck in HOSPITALS_ROOT:
        cm = float(HOSPITALS_ROOT[ck].get("cost_modifier", 1.0))
    else:
        cm = float((CITIES.get("default") or HOSPITALS_ROOT.get("default") or {}).get("cost_modifier", 1.0))
    return (int(base[0]*cm), int(base[1]*cm))

def nearby_hospitals(city: str) -> List[Dict[str,Any]]:
    ck = city_key(city)
    if CITIES and ck in CITIES:
        return CITIES[ck].get("hospitals", []) or []
    if HOSPITALS_ROOT and ck in HOSPITALS_ROOT:
        return HOSPITALS_ROOT[ck].get("hospitals", []) or []
    # fallback to default
    if CITIES and "default" in CITIES:
        return CITIES["default"].get("hospitals", []) or []
    if HOSPITALS_ROOT and "default" in HOSPITALS_ROOT:
        return HOSPITALS_ROOT["default"].get("hospitals", []) or []
    return []

def ascii_safe(s: str) -> str:
    if not s:
        return ""
    table = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "•": "*", "…": "...", "₹": "Rs ", "\u00a0": " "}
    out = str(s)
    for k, v in table.items():
        out = out.replace(k, v)
    return out.encode("ascii", "replace").decode("ascii")

# -----------------------------
# Extraction (safe OCR)
# -----------------------------
def extract_text_from_file(uploaded) -> Tuple[str, List[str]]:
    warnings = []
    try:
        name = uploaded.name.lower()
    except Exception:
        name = ""
    data = uploaded.read()

    # PDF
    if name.endswith(".pdf"):
        try:
            text = ""
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for page in pdf.pages:
                    text += "\n" + (page.extract_text() or "")
            return text.strip(), warnings
        except Exception as e:
            return "", [f"PDF read error: {e}"]

    # DOCX
    if name.endswith(".docx"):
        try:
            buf = io.BytesIO(data)
            return (docx2txt.process(buf) or ""), warnings
        except Exception as e:
            return "", [f"DOCX read error: {e}"]

    # IMAGE -> OCR
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        if OCR_AVAILABLE:
            try:
                text = pytesseract.image_to_string(im)
                return text or "", warnings
            except Exception as e:
                warnings.append(f"OCR failed: {e}")
                return "", warnings
        else:
            warnings.append("OCR not available. Install pytesseract + system Tesseract to enable image OCR.")
            return "", warnings
    except Exception:
        return "", ["Unsupported file. Upload PDF / DOCX / JPG / PNG."]

# -----------------------------
# Parsing & detection (YAML-driven)
# -----------------------------
def parse_entities(text: str) -> Dict[str, Any]:
    ents: Dict[str, Any] = {}
    m = re.search(r"(?i)\b(patient\s*name|name)\s*[:\-]\s*([A-Za-z ,.'-]{2,60})", text or "")
    ents["Name"] = (m.group(2).strip() if m else "")
    m = re.search(r"(?i)\b(age)\s*[:\-]\s*(\d{1,3})", text or "")
    ents["Age"] = (m.group(2) if m else "")
    m = re.search(r"(?i)\b(sex|gender)\s*[:\-]\s*(male|female|m|f|other)", text or "")
    ents["Sex"] = (m.group(2).capitalize() if m else "")
    return ents

def summarize_problems(text: str) -> List[str]:
    probs = []
    for pat in [r"(?i)impression\s*[:\-]\s*(.+)",
                r"(?i)diagnosis\s*[:\-]\s*(.+)",
                r"(?i)conclusion\s*[:\-]\s*(.+)",
                r"(?i)findings\s*[:\-]\s*(.+)"]:
        for m in re.finditer(pat, text or ""):
            s = m.group(1).strip()
            if s and s not in probs:
                probs.append(s[:300])
    if not probs:
        lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
        keywords = ["pain","lesion","fracture","mass","infection","infarct","tear","hernia","stone","blockage","tumor","ischemia","angina","colic"]
        guesses = [l for l in lines if any(w in l.lower() for w in keywords)]
        probs = list(dict.fromkeys(guesses[:3]))
    return probs

def word_hit(hay: str, needle: str) -> bool:
    if not needle or len(needle.strip()) < 2:
        return False
    return re.search(rf"\b{re.escape(needle.lower())}\b", hay) is not None

def detect_conditions(text: str) -> List[Dict]:
    t = normalize_text(text)
    results = []
    for d in DISEASES:
        kws = d.get("keywords", []) or []
        hits = [kw for kw in kws if word_hit(t, kw)]
        if hits:
            results.append({
                "name": d.get("name"),
                "hits": hits,
                "procedures": d.get("procedures", []),
                "recovery_recos": d.get("recovery_recos", []),
                "severity_rules": d.get("severity_rules", {}),
                "cost_inr": d.get("cost_inr", [0,0]),
                "about": d.get("about", "")
            })
    results.sort(key=lambda x: len(x["hits"]), reverse=True)
    return results

def severity_percent(text: str, cond: Dict) -> int:
    t = normalize_text(text)
    disease_reds = [r.lower() for r in (cond.get("severity_rules", {}).get("red_flags", []) or [])]
    general_reds = [r.lower() for r in (GENERAL_RULES.get("red_flags", []) or [])]
    signals = ["severe","acute","sudden","worsening","emergency","fever","syncope","vomiting","bleeding","dyspnea","chest pain","unstable","shock","collapse","sepsis","uncontrolled","hypotension","tachycardia"]
    s = 0
    for rf in general_reds:
        if rf and rf in t:
            s += 3
    for rf in disease_reds:
        if rf and rf in t:
            s += 3
    s += sum(1 for w in signals if w in t)
    hits_boost = min(5, len(cond.get("hits", [])))
    pct = s * 8 + hits_boost * 10
    if len(cond.get("hits", [])) > 0 and pct < 10:
        pct = 10
    pct = max(0, min(95, pct))
    if any(p in t for p in ["normal study", "within normal limits", "no acute", "impression: normal", "normal chest xray", "normal study"]):
        pct = min(pct, 5)
    return int(pct)

# -----------------------------
# Bookings persistence (JSON)
# -----------------------------
def load_bookings() -> List[Dict[str,str]]:
    if not os.path.exists(BOOKINGS_FILE):
        return []
    try:
        with open(BOOKINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data or []
    except Exception:
        return []

def save_bookings(rows: List[Dict[str,str]]):
    try:
        with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

def slot_taken(rows: List[Dict[str,str]], hospital: str, doctor: str, dt_str: str, tm_str: str) -> bool:
    if not rows:
        return False
    for r in rows:
        if r.get("hospital")==hospital and r.get("doctor")==doctor and r.get("date")==dt_str and r.get("time")==tm_str:
            return True
    return False

def cancel_booking(booking_id: str) -> bool:
    rows = load_bookings()
    new_rows = [r for r in rows if r.get("booking_id") != booking_id]
    if len(new_rows) != len(rows):
        save_bookings(new_rows)
        return True
    return False

# -----------------------------
# PDF / ICS builders
# -----------------------------
def build_full_pdf(entities: Dict, problems: List[str], best: Dict, city: str,
                   hospitals: List[str], appt: Dict) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, ascii_safe("Clinical Report Helper (Educational, India) — Full Report"), ln=True)
    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 7, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True)

    def sec(title):
        pdf.set_font("Arial", "B", 12); pdf.ln(3); pdf.cell(0, 8, ascii_safe(title), ln=True)
        pdf.set_font("Arial", "", 11)

    sec("Patient")
    pdf.multi_cell(0, 6, ascii_safe(f"Name: {entities.get('Name','') or '—'}"))
    pdf.multi_cell(0, 6, ascii_safe(f"Age/Sex: {entities.get('Age','') or '—'} / {entities.get('Sex','') or '—'}"))
    pdf.multi_cell(0, 6, ascii_safe(f"City: {city or '—'}"))

    sec("Issues / Impressions")
    for p in problems or ["—"]:
        pdf.multi_cell(0, 6, ascii_safe(f"* {p}"))

    sec("Condition & Care Plan (informational)")
    if best:
        pdf.multi_cell(0, 6, ascii_safe(f"Likely condition: {best.get('name','—')}"))
        pdf.multi_cell(0, 6, ascii_safe(f"About: {best.get('about','—')}"))
        pdf.multi_cell(0, 6, ascii_safe(f"Typical procedures: {', '.join(best.get('procedures', []) or [])}"))
        pdf.multi_cell(0, 6, ascii_safe(f"Recovery: {' | '.join(best.get('recovery_recos', []) or [])}"))
        pdf.multi_cell(0, 6, ascii_safe(f"Severity: {best.get('severity_pct','—')}%"))
        lo, hi = india_adjust_cost(best.get("cost_inr", [0,0]), city)
        pdf.multi_cell(0, 6, ascii_safe(f"Estimated cost (INR): Rs {lo:,} – Rs {hi:,}"))
    else:
        pdf.multi_cell(0, 6, ascii_safe("No condition pattern matched, or report appears normal."))

    sec("Suggested Hospitals")
    for h in hospitals or ["—"]:
        pdf.multi_cell(0, 6, ascii_safe(f"* {h}"))

    sec("Appointment Details")
    pdf.multi_cell(0, 6, ascii_safe(f"Hospital: {appt.get('hospital','') or '—'}"))
    pdf.multi_cell(0, 6, ascii_safe(f"Scheduled for: {appt.get('date','—')} at {appt.get('time','—')}"))
    pdf.multi_cell(0, 6, ascii_safe(f"Phone: {appt.get('phone','—')}"))
    pdf.multi_cell(0, 6, ascii_safe(f"Email: {appt.get('email','—')}"))

    sec("Disclaimer")
    pdf.multi_cell(0, 6, ascii_safe("Informational only — NOT a medical diagnosis. Consult a qualified clinician."))
    return pdf.output(dest="S").encode("latin-1", "replace")

def build_receipt_pdf(booking: Dict[str,str]) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, ascii_safe("Appointment Booking Receipt"), ln=True)
    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 7, f"Receipt ID: {booking.get('booking_id')}", ln=True)
    pdf.cell(0, 7, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True)

    def row(k, v):
        pdf.set_font("Arial", "B", 11); pdf.cell(55, 7, ascii_safe(k)+":")
        pdf.set_font("Arial", "", 11); pdf.multi_cell(0, 7, ascii_safe(v))

    pdf.ln(2)
    row("Patient", booking.get("patient_name","—"))
    row("Phone", booking.get("patient_phone","—"))
    row("Email", booking.get("patient_email","—"))
    row("City", booking.get("city","—"))
    row("Hospital", booking.get("hospital","—"))
    row("Department", booking.get("department","—"))
    row("Doctor", booking.get("doctor","—"))
    row("Date", booking.get("date","—"))
    row("Time", booking.get("time","—"))
    pdf.ln(2)
    pdf.set_font("Arial", "", 10)
    pdf.multi_cell(0, 6, ascii_safe("Please arrive 15 minutes early with ID and past reports. This is a confirmation of your requested slot; the hospital may call you for any changes."))
    return pdf.output(dest="S").encode("latin-1", "replace")

def build_ics(patient_name: str, city: str, hospital: str, specialist: str,
              appt_dt: datetime) -> bytes:
    end_dt = appt_dt + timedelta(minutes=30)
    ics = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Clinical Report Helper//EN
BEGIN:VEVENT
UID:{uuid.uuid4()}
DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}
DTSTART:{appt_dt.strftime('%Y%m%dT%H%M%S')}
DTEND:{end_dt.strftime('%Y%m%dT%H%M%S')}
SUMMARY:Appointment for {patient_name or 'Patient'} with {specialist} at {hospital}, {city}
DESCRIPTION:Auto-generated from Clinical Report Helper. Bring your reports and ID.
LOCATION:{hospital}, {city}
END:VEVENT
END:VCALENDAR"""
    return ics.encode("utf-8")

# -----------------------------
# Email — flexible SMTP
# -----------------------------
def send_email_flexible(sender_email: str, sender_password: str,
                        to_email: str, subject: str, body: str,
                        attachments: List[Tuple[bytes, str, str]],
                        bcc_self: bool,
                        host: str, port: int, security_mode: str, timeout: int) -> Tuple[bool, str]:
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = to_email
    if bcc_self:
        msg["Bcc"] = sender_email
    msg.set_content(body)

    for data, fname, mime in attachments:
        maintype, subtype = mime.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=fname)

    try:
        context = ssl.create_default_context()
        if security_mode.startswith("STARTTLS"):
            with smtplib.SMTP(host=host, port=port, timeout=timeout) as server:
                server.ehlo(); server.starttls(context=context); server.ehlo()
                server.login(sender_email, sender_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP_SSL(host=host, port=port, context=context, timeout=timeout) as server:
                server.login(sender_email, sender_password)
                server.send_message(msg)
        return True, "Email sent."
    except Exception as e:
        if security_mode.startswith("STARTTLS") and port == 587:
            try:
                with smtplib.SMTP_SSL(host=host, port=465, context=ssl.create_default_context(), timeout=timeout) as server:
                    server.login(sender_email, sender_password)
                    server.send_message(msg)
                return True, "Email sent (fallback SSL:465)."
            except Exception as e2:
                return False, f"Email failed on STARTTLS:587 and SSL:465.\nPrimary: {e}\nFallback: {e2}"
        return False, f"Email failed: {e}"

# -----------------------------
# Sidebar — India city
# -----------------------------
st.sidebar.header("India Location")
cities = [""] + list_india_cities()
city = st.sidebar.selectbox("Choose your city (India)", cities, index=(cities.index("chennai") if "chennai" in cities else 0))
st.sidebar.caption("Used for cost estimates, hospital suggestions, and booking directory.")

# -----------------------------
# Main UI
# -----------------------------
st.title("🩺 Clinical Report Helper (India)")
st.write('<span class="small-muted">For education/information only. Not medical advice.</span>', unsafe_allow_html=True)
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)

colL, colR = st.columns([2,1], vertical_alignment="top")

with colL:
    st.markdown('<div class="section-title">1) Upload your report</div>', unsafe_allow_html=True)
    uploaded = st.file_uploader("Upload PDF, DOCX, or a clear image (JPG/PNG)", type=["pdf","docx","jpg","jpeg","png"])

with colR:
    st.markdown('<div class="section-title">Status</div>', unsafe_allow_html=True)
    if st.session_state.extracted_text:
        st.success("Report text extracted")
    else:
        st.info("Waiting for upload…")

# Auto-run extract
if uploaded is not None:
    text, warns = extract_text_from_file(uploaded)
    st.session_state.extracted_text = text
    if warns:
        st.warning("\n".join(warns))

if st.session_state.extracted_text:
    # Parse & detect
    ents = parse_entities(st.session_state.extracted_text)
    probs = summarize_problems(st.session_state.extracted_text)

    tn = normalize_text(st.session_state.extracted_text)
    normal_markers = [
        "normal chest x-ray", "normal chest xray", "normal chest x-ray study",
        "no acute cardiopulmonary", "no focal consolidation", "no pleural effusion",
        "within normal limits", "impression: normal", "impression : normal", "normal study"
    ]
    detected = [] if any(p in tn for p in normal_markers) else detect_conditions(st.session_state.extracted_text)

    best = None
    alts = []
    if detected:
        for c in detected:
            sev_pct = severity_percent(st.session_state.extracted_text, c)
            lo, hi = india_adjust_cost(c.get("cost_inr", [0,0]), city)
            c["severity_pct"] = sev_pct
            c["cost_low"] = lo
            c["cost_high"] = hi
            alts.append(c)
        alts.sort(key=lambda x: (len(x.get("hits", [])), x.get("severity_pct", 0)), reverse=True)
        best = alts[0]
        st.session_state.alt_conditions = alts[1:]
    else:
        st.session_state.alt_conditions = []

    st.session_state.entities = ents
    st.session_state.problems = probs
    st.session_state.best_condition = best

    # Hospitals list (names only for display)
    hosp_objs = nearby_hospitals(city)
    hospitals_list = [h.get("name","") for h in hosp_objs]
    st.session_state.hospitals = hospitals_list

    # ---------- 2) Patient summary ----------
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">2) Patient summary</div>', unsafe_allow_html=True)
    c1, c2 = st.columns([1.2, 2], vertical_alignment="top")

    with c1:
        df_patient = pd.DataFrame([
            ["Name", ents.get("Name","") or "—"],
            ["Age",  ents.get("Age","") or "—"],
            ["Sex",  ents.get("Sex","") or "—"],
            ["City (India)", city or "—"],
        ], columns=["Field", "Value"])
        st.table(df_patient)

    with c2:
        df_probs = pd.DataFrame({"Problem / Impression": st.session_state.problems or ["—"]})
        st.dataframe(df_probs, use_container_width=True)

    # ---------- 3) Condition & plan ----------
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">3) Condition & care plan (informational)</div>', unsafe_allow_html=True)

    if best:
        pct = best.get("severity_pct", 0)
        sev_color = "#047857" if pct < 34 else ("#b45309" if pct < 67 else "#b91c1c")
        st.markdown(
            f'<div class="card"><span class="sev-tag" style="background:#f3f4f6;color:{sev_color};">Severity: {pct}%</span></div>',
            unsafe_allow_html=True
        )
        df_cond = pd.DataFrame([
            ["Likely condition", best.get("name")],
            ["About", best.get("about","")],
            ["What to do now", " ; ".join(best.get("procedures", []) or [])],
            ["Recovery (typical)", " | ".join(best.get("recovery_recos", []) or [])],
            ["Estimated cost (INR)", f"₹{best.get('cost_low',0):,} – ₹{best.get('cost_high',0):,}"],
        ], columns=["Item", "Details"])
        st.table(df_cond)
    else:
        st.warning("No specific condition pattern matched. If your report says 'Normal', this can be expected. Otherwise, consult a clinician for personalized advice.")

    # ---------- 4) Hospitals ----------
    st.markdown('<div class="section-title">4) Suggested hospitals (India)</div>', unsafe_allow_html=True)
    st.dataframe(pd.DataFrame({"Hospitals": st.session_state.hospitals or ["—"]}), use_container_width=True)

    # ---------- 5) Appointment Booking ----------
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">5) Hospital Appointment Booking</div>', unsafe_allow_html=True)

    hosp_dir = hosp_objs or (CITIES.get("default", {}) or HOSPITALS_ROOT.get("default", {})).get("hospitals", [])
    if not hosp_dir:
        st.info("Select a city with hospitals in the sidebar to enable booking.")
    else:
        hospital_names = [h.get("name","") for h in hosp_dir]
        h_idx = st.selectbox("Choose a hospital", list(range(len(hospital_names))),
                             format_func=lambda i: hospital_names[i])
        chosen_h = hosp_dir[h_idx]
        hospital_name = chosen_h.get("name","")
        hospital_email = chosen_h.get("email","")

        # Suggest department from condition name heuristics
        suggested_dept = None
        if best:
            spec = (best.get("name","") or "").lower()
            if "card" in spec or "coronary" in spec: suggested_dept = "Cardiology"
            elif "urolog" in spec or "renal" in spec or "stone" in spec: suggested_dept = "Urology"
            elif "spine" in spec or "neuro" in spec: suggested_dept = "Spine/Neuro"
            elif "ent" in spec or "sinus" in spec: suggested_dept = "ENT"
            elif "cataract" in spec or "ophth" in spec: suggested_dept = "Ophthalmology"
            elif "orth" in spec or "acl" in spec: suggested_dept = "Orthopaedics"
            elif "append" in spec or "hernia" in spec or "gall" in spec: suggested_dept = "General Surgery"

        dept_names = list(chosen_h.get("departments", {}).keys())
        dept_default = dept_names.index(suggested_dept) if (suggested_dept in dept_names) else 0 if dept_names else 0
        department = st.selectbox("Department", dept_names or ["General"], index=dept_default)
        doctors = chosen_h.get("departments", {}).get(department, ["Duty Doctor"])
        doctor = st.selectbox("Doctor", doctors)

        rows = load_bookings()
        colA, colB = st.columns(2)
        with colA:
            appt_date = st.date_input("Choose date", value=date.today(), min_value=date.today(), max_value=date.today()+timedelta(days=14))
        with colB:
            slots = []
            start_dt = datetime.combine(appt_date, time(9,0))
            end_dt = datetime.combine(appt_date, time(17,0))
            cur = start_dt
            while cur < end_dt:
                slots.append(cur.strftime("%H:%M"))
                cur += timedelta(minutes=30)
            free_slots = [s for s in slots if not slot_taken(rows, hospital_name, doctor, str(appt_date), s)]
            appt_time = st.selectbox("Available time", free_slots or ["No slots available"])

        colP1, colP2, colP3 = st.columns(3)
        with colP1:
            patient_name = st.text_input("Patient name", value=ents.get("Name",""))
        with colP2:
            patient_phone = st.text_input("Phone (optional)")
        with colP3:
            patient_email = st.text_input("Email (optional)")

        if st.button("✅ Confirm Booking"):
            if not free_slots or appt_time not in free_slots:
                st.error("Selected time is not available.")
            else:
                booking_id = str(uuid.uuid4())[:8]
                new_row = {
                    "booking_id": booking_id,
                    "patient_name": patient_name,
                    "patient_phone": patient_phone,
                    "patient_email": patient_email,
                    "city": city,
                    "hospital": hospital_name,
                    "department": department,
                    "doctor": doctor,
                    "date": str(appt_date),
                    "time": appt_time
                }
                rows.append(new_row)
                save_bookings(rows)

                st.success(f"Booked! ID: {booking_id}")
                receipt_pdf = build_receipt_pdf(new_row)
                st.session_state.receipt_pdf_bytes = receipt_pdf

                spec = department
                appt_dt = datetime.combine(appt_date, datetime.strptime(appt_time, "%H:%M").time())
                ics_bytes = build_ics(patient_name, city, hospital_name, spec, appt_dt)
                st.session_state.latest_ics_bytes = ics_bytes

                st.download_button("⬇️ Download Booking Receipt (PDF)",
                                   data=receipt_pdf, file_name=f"booking_{booking_id}.pdf", mime="application/pdf")
                st.download_button("⬇️ Add to Calendar (.ics)",
                                   data=ics_bytes, file_name=f"booking_{booking_id}.ics", mime="text/calendar")

                st.info("Use the Email section below to email the hospital with attachments.")

    # ---------- 6) Full report ----------
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">6) Full report download</div>', unsafe_allow_html=True)

    appt_info = {"hospital": hospitals_list[0] if hospitals_list else "", "date": "", "time": "", "phone": "", "email": ""}
    full_pdf = build_full_pdf(st.session_state.entities, st.session_state.problems, st.session_state.best_condition, city, st.session_state.hospitals, appt_info)
    st.session_state.latest_pdf_bytes = full_pdf

    st.download_button("⬇️ Download Full Report (PDF)",
                       data=full_pdf, file_name="clinical_full_report.pdf", mime="application/pdf")

    # ---------- Email booking / confirmation ----------
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Email booking / confirmation</div>', unsafe_allow_html=True)

    colE1, colE2 = st.columns(2)
    with colE1:
        sender_email = st.text_input("Your email (SMTP user) e.g. yourname@gmail.com")
        sender_pass  = st.text_input("App password / SMTP password", type="password")
        send_copy_to_me = st.checkbox("Send me a copy (BCC)", value=True)
    with colE2:
        fallback_email = (hosp_dir[0].get("email","") if hosp_dir else "")
        hospital_email = st.text_input("Hospital / Recipient email", value=fallback_email)
        email_subject  = st.text_input("Email subject", value="Appointment Booking Request")

    with st.expander("Advanced SMTP settings"):
        smtp_host = st.text_input("SMTP host", value="smtp.gmail.com")
        smtp_port = st.number_input("SMTP port", min_value=1, max_value=65535, value=587, step=1)
        security = st.selectbox("Security", ["STARTTLS (recommended, port 587)", "SSL/TLS (port 465)"])
        smtp_timeout = st.number_input("Timeout (seconds)", min_value=5, max_value=120, value=25)

    body_lines = ["Dear Scheduling Team,", ""]
    if len(st.session_state.problems) > 0:
        body_lines.append("Report highlights: " + "; ".join(st.session_state.problems[:3]))
    if st.session_state.best_condition:
        body_lines.append(f"Possible condition (non-diagnostic): {st.session_state.best_condition.get('name')} | Severity: {st.session_state.best_condition.get('severity_pct')}%")
    body_lines.append("Please find attached a booking receipt (if I booked a slot) and my clinical summary.")
    body_lines += ["", "Thank you,", st.session_state.entities.get("Name","Patient") or "Patient"]
    email_body = "\n".join(body_lines)
    st.code(email_body)

    attachments = [(full_pdf, "clinical_full_report.pdf", "application/pdf")]
    if st.session_state.receipt_pdf_bytes:
        attachments.append((st.session_state.receipt_pdf_bytes, "booking_receipt.pdf", "application/pdf"))
    if st.session_state.latest_ics_bytes:
        attachments.append((st.session_state.latest_ics_bytes, "appointment.ics", "text/calendar"))

    if st.button("📧 Send Email with Attachments"):
        if not (sender_email and sender_pass and hospital_email):
            st.error("Please fill: Your email, password, and Hospital email.")
        else:
            ok, msg = send_email_flexible(
                sender_email=sender_email,
                sender_password=sender_pass,
                to_email=hospital_email,
                subject=email_subject,
                body=email_body,
                attachments=attachments,
                bcc_self=send_copy_to_me,
                host=smtp_host,
                port=int(smtp_port),
                security_mode=security,
                timeout=int(smtp_timeout),
            )
            st.success(msg) if ok else st.error(msg)

# ---------- My Bookings ----------
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
st.markdown('<div class="section-title">My Bookings</div>', unsafe_allow_html=True)

all_rows = load_bookings()
if not all_rows:
    st.info("No bookings yet.")
else:
    st.dataframe(pd.DataFrame(all_rows), use_container_width=True)
    cancel_id = st.text_input("Enter Booking ID to cancel")
    if st.button("Cancel Booking"):
        if cancel_id.strip():
            ok = cancel_booking(cancel_id.strip())
            if ok:
                st.success("Booking cancelled.")
            else:
                st.error("Booking ID not found.")
        else:
            st.error("Please enter a Booking ID.")

# Footer
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
st.caption("© 2025 — For education/information only. Not a medical device; not medical advice.")
