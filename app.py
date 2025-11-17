# app.py
# -------------------------------------------------------------------
# Clinical Report Helper (Educational, India) — with Appointment Booking System
# - Upload (PDF/DOCX/Image) → instant tables (no button)
# - Extract Name/Age/Sex/Problems
# - Detect likely condition (rule-based, India pack)
# - Show About, What to do, Recovery, Severity % (no red flags), Cost (INR by city)
# - India-only city list & hospitals
# - NEW: Hospital Appointment Booking System (directory, doctors, slot picker, conflict check)
# - Persist bookings to bookings.csv (+ cancel), show "My Bookings"
# - Full Report PDF + Booking Receipt PDF + .ics calendar
# - Flexible SMTP email (attach PDFs + .ics)
# IMPORTANT: Informational only. Not a medical device; Not medical advice.
# -------------------------------------------------------------------

import streamlit as st
import io, re, json, uuid, os
from typing import Dict, List, Tuple, Any
from datetime import datetime, date, time, timedelta

import pandas as pd
import pdfplumber
import docx2txt
from PIL import Image

# Optional OCR
try:
    import pytesseract
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False

from fpdf import FPDF
import smtplib, ssl
from email.message import EmailMessage

BOOKINGS_CSV = "bookings.csv"

# -----------------------------
# Page & minimal styling
# -----------------------------
st.set_page_config(page_title="Clinical Report Helper (India)", page_icon="🩺", layout="wide")
st.markdown("""
<style>
.small-muted {color:#6b7280;font-size:12px;}
.card {border:1px solid #e5e7eb;border-radius:12px;padding:14px;margin-top:8px;}
.section-title {font-weight:600;font-size:18px;margin-top:8px;margin-bottom:0px;}
.big-badge {display:inline-block;background:#eef2ff;color:#3730a3;padding:6px 10px;border-radius:999px;font-weight:600;}
.hr {height:1px;background:#e5e7eb;border:none;margin:16px 0;}
.sev-tag {display:inline-block;border-radius:10px;padding:4px 10px;font-weight:700;}
.ok {color:#065f46;}
.warn {color:#9a3412;}
.err {color:#991b1b;}
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
    "email_draft": "",
    "latest_pdf_bytes": b"",
    "latest_ics_bytes": b"",
    "receipt_pdf_bytes": b"",
}
for k, v in defaults.items():
    if k not in st.session_state:
        st.session_state[k] = v

# -----------------------------
# India KB (cities, hospitals, conditions)
# -----------------------------
KB: Dict[str, Any] = {
    "city_cost_modifiers": {
        "mumbai": 1.25, "delhi": 1.2, "new delhi": 1.2, "gurgaon": 1.2, "noida": 1.15,
        "bengaluru": 1.2, "bangalore": 1.2, "chennai": 1.15, "hyderabad": 1.15, "pune": 1.15,
        "kolkata": 1.15, "ahmedabad": 1.1, "jaipur": 1.1, "kochi": 1.1, "trivandrum": 1.1,
        "coimbatore": 1.05, "madurai": 1.05, "trichy": 1.05, "tiruchirappalli": 1.05, "karur": 1.0,
        "surat": 1.05, "indore": 1.05, "bhopal": 1.05, "lucknow": 1.05, "kanpur": 1.05,
        "chandigarh": 1.1, "ludhiana": 1.05, "amritsar": 1.05, "nagpur": 1.05, "vizag": 1.05,
        "bhubaneswar": 1.05, "ranchi": 1.0, "patna": 1.0, "guwahati": 1.0, "mysuru": 1.0,
        "vadodara": 1.05, "nashik": 1.05, "rajkot": 1.05, "vellore": 1.05, "salem": 1.0,
        "default": 1.0
    },
    # Hospital directory now includes departments, doctors & contact email.
    "hospitals": {
        "mumbai": [
            {"name":"Kokilaben Hospital", "email":"appointments@kokilaben.in",
             "departments":{"Cardiology":["Dr. A Sharma","Dr. K Rao"], "General Surgery":["Dr. V Singh"]}},
            {"name":"Nanavati Max", "email":"book@nanavatimax.in",
             "departments":{"Cardiology":["Dr. M Iyer"], "Orthopaedics":["Dr. P Varma"]}},
            {"name":"Jaslok Hospital", "email":"clinic@jaslokhospital.net",
             "departments":{"Urology":["Dr. R Patil"], "ENT":["Dr. S Desai"]}},
        ],
        "delhi": [
            {"name":"Max Saket", "email":"bookings@maxhealthcare.com",
             "departments":{"Cardiology":["Dr. T Mehta"], "General Surgery":["Dr. H Arora"], "Urology":["Dr. P Gupta"]}},
            {"name":"Fortis Escorts Okhla", "email":"appointments@fortishealthcare.com",
             "departments":{"Cardiology":["Dr. V Khanna"], "Orthopaedics":["Dr. R Kapoor"]}},
        ],
        "chennai": [
            {"name":"Apollo Greams Road", "email":"apollo.chennai@apollohospitals.com",
             "departments":{"Cardiology":["Dr. R Krishnan"], "General Surgery":["Dr. S Balan"], "ENT":["Dr. N Kannan"]}},
            {"name":"Fortis Malar", "email":"appointments.chennai@fortishealthcare.com",
             "departments":{"Urology":["Dr. A Menon"], "Orthopaedics":["Dr. B Srinivasan"]}},
            {"name":"MIOT International", "email":"book@miotinternational.com",
             "departments":{"Spine/Neuro":["Dr. G Anand"], "General Surgery":["Dr. J Varadarajan"]}},
        ],
        "bengaluru": [
            {"name":"Manipal Old Airport Road", "email":"appointments.blr@manipalhospitals.com",
             "departments":{"Cardiology":["Dr. N Kumar"], "Spine/Neuro":["Dr. Y Shetty"]}},
            {"name":"Aster CMI", "email":"bookings@astercmihospital.com",
             "departments":{"General Surgery":["Dr. L Rao"], "Urology":["Dr. I Ahmed"]}},
            {"name":"Fortis Bannerghatta", "email":"contact.bg@fortishealthcare.com",
             "departments":{"Orthopaedics":["Dr. R Dinesh"], "ENT":["Dr. P Reddy"]}},
        ],
        "coimbatore": [
            {"name":"PSG Hospitals", "email":"psg.appt@psghospitals.com",
             "departments":{"General Surgery":["Dr. M Mohan"], "Urology":["Dr. R Kumar"]}},
            {"name":"KMCH", "email":"book@kmchhospitals.com",
             "departments":{"Cardiology":["Dr. J Thomas"], "Spine/Neuro":["Dr. S Kumar"]}},
            {"name":"GKNM", "email":"appointments@gknmhospital.org",
             "departments":{"Orthopaedics":["Dr. A Kannan"], "ENT":["Dr. P Natarajan"]}},
        ],
        # ... add more cities as needed ...
        "default": [
            {"name":"Accredited tertiary center near you", "email":"", "departments":{"General":["Duty Doctor"]}}
        ],
    },
    "conditions": {
        "appendicitis": {
            "display": "Acute Appendicitis",
            "keywords": ["appendicitis","appendix","rlq pain","mcburney","appendectomy"],
            "about": "Inflammation of the appendix causing right lower abdominal pain and fever.",
            "actions": ["Urgent surgical evaluation","IV fluids & antibiotics per clinician","Nil by mouth if surgery planned"],
            "recovery": ["Discharge ~24–48h after lap surgery","Light activity in a few days","Avoid heavy lifting 2–4 weeks"],
            "specialist": "General Surgeon",
            "surgeries": ["Laparoscopic appendectomy","Open appendectomy (selected)"],
            "cost_inr": [60000, 250000]
        },
        "cholelithiasis": {
            "display": "Gallstones / Cholecystitis",
            "keywords": ["gallstones","cholelithiasis","biliary colic","cholecystitis","cholecystectomy"],
            "about": "Stones in the gallbladder causing pain, sometimes infection (cholecystitis).",
            "actions": ["Surgical consult","Pain control; antibiotics if infected","Low-fat diet initially"],
            "recovery": ["Same/next-day discharge common","Desk work ~1 week","Strenuous activity 2–4 weeks"],
            "specialist": "General Surgeon",
            "surgeries": ["Laparoscopic cholecystectomy"],
            "cost_inr": [80000, 300000]
        },
        "inguinal_hernia": {
            "display": "Inguinal Hernia",
            "keywords": ["inguinal hernia","groin bulge","hernioplasty","mesh repair"],
            "about": "Weakness in the groin wall allowing tissue to bulge out; risk of strangulation.",
            "actions": ["Elective surgical repair for symptoms","Urgent care if painful irreducible bulge"],
            "recovery": ["Light activity 1–2 weeks","Avoid heavy lifting 4–6 weeks"],
            "specialist": "General Surgeon",
            "surgeries": ["Lap TEP/TAPP mesh repair","Open Lichtenstein mesh repair"],
            "cost_inr": [60000, 200000]
        },
        "renal_stone": {
            "display": "Kidney/Ureteric Stones",
            "keywords": ["renal stone","kidney stone","ureteric stone","pcnl","urs","eswl"],
            "about": "Crystals forming stones in kidney/ureter causing colicky pain; may block urine.",
            "actions": ["Urology evaluation","Hydration/pain control","Decompression if infected obstruction"],
            "recovery": ["Back to routine ~3–7 days (after URS/ESWL)","Follow stone-prevention advice"],
            "specialist": "Urologist",
            "surgeries": ["URS + laser lithotripsy","PCNL","ESWL (selected)"],
            "cost_inr": [60000, 250000]
        },
        "uterine_fibroids": {
            "display": "Uterine Fibroids",
            "keywords": ["fibroid","leiomyoma","myomectomy","hysterectomy"],
            "about": "Non-cancerous uterine growths causing bleeding, pain, or fertility issues.",
            "actions": ["Gynecology consult","Assess size/symptoms/fertility plans"],
            "recovery": ["Avoid heavy work 4–6 weeks post major surgery"],
            "specialist": "Gynecologist",
            "surgeries": ["Laparoscopic myomectomy","Hysterectomy (lap/open)"],
            "cost_inr": [120000, 500000]
        },
        "acl_tear": {
            "display": "ACL Tear",
            "keywords": ["acl","anterior cruciate ligament","arthroscopy","reconstruction"],
            "about": "Tear of knee’s ACL causing instability; athletes often need reconstruction.",
            "actions": ["Ortho consult","Brace/physio; surgery if instability"],
            "recovery": ["Rehab 4–6 months for sport return"],
            "specialist": "Orthopaedic Surgeon",
            "surgeries": ["Arthroscopic ACL reconstruction"],
            "cost_inr": [150000, 400000]
        },
        "lumbar_disc_herniation": {
            "display": "Lumbar Disc Herniation (Sciatica)",
            "keywords": ["lumbar disc","l4-l5","l5-s1","sciatica","microdiscectomy"],
            "about": "Slipped disc compressing nerve roots causing back pain radiating to leg.",
            "actions": ["Spine/neuro consult","Conservative therapy first; surgery if deficits/persistent pain"],
            "recovery": ["Avoid heavy lifting 4–6 weeks post-op; core strengthening"],
            "specialist": "Spine/Neurosurgeon",
            "surgeries": ["Microdiscectomy (selected)"],
            "cost_inr": [150000, 400000]
        },
        "cad": {
            "display": "Coronary Artery Disease",
            "keywords": ["coronary","cad","angina","nstemi","stemi","myocardial infarction","mi"],
            "about": "Narrowing/clot in heart arteries causing angina or heart attack.",
            "actions": ["Emergency if chest pain at rest","Cardiology consult","ECG/troponin; anti-ischemic meds"],
            "recovery": ["Cardiac rehab; risk-factor control; adherence to meds"],
            "specialist": "Cardiologist / CTVS",
            "surgeries": ["PCI (angioplasty + stent)","CABG (bypass) — selected"],
            "cost_inr": [150000, 900000]
        },
        "dns_sinusitis": {
            "display": "Deviated Septum / Chronic Sinusitis",
            "keywords": ["deviated septum","dns","septoplasty","fess","sinusitis"],
            "about": "Nasal septum deviation or chronic sinus inflammation causing blockage/infections.",
            "actions": ["ENT consult","Nasal steroids/irrigation; surgery if failure"],
            "recovery": ["Nasal care/irrigation; avoid nose-blowing early"],
            "specialist": "ENT Surgeon",
            "surgeries": ["Septoplasty","FESS"],
            "cost_inr": [60000, 250000]
        },
        "cataract": {
            "display": "Cataract",
            "keywords": ["cataract","iols","phacoemulsification"],
            "about": "Clouding of eye lens leading to gradual visual impairment.",
            "actions": ["Ophthalmology consult","Surgery if vision function limited"],
            "recovery": ["Eye drops regimen; protect eye 1–2 weeks"],
            "specialist": "Ophthalmologist",
            "surgeries": ["Phaco + IOL"],
            "cost_inr": [20000, 120000]
        },
        "breast_lump": {
            "display": "Breast Lump (suspicious/large)",
            "keywords": ["breast lump","lumpectomy","mastectomy"],
            "about": "Breast mass—needs imaging/biopsy to rule out cancer; surgery per stage.",
            "actions": ["Breast/Onco consult","Imaging + core biopsy"],
            "recovery": ["Drain care; arm exercises as advised"],
            "specialist": "Breast/Onco Surgeon",
            "surgeries": ["Lumpectomy","Mastectomy (team-based)"],
            "cost_inr": [120000, 600000]
        },
    }
}

# -----------------------------
# Utilities
# -----------------------------
def normalize_text(t: str) -> str:
    return re.sub(r"\s+", " ", t or "").strip().lower()

def list_india_cities() -> List[str]:
    ks = [k for k in KB["hospitals"].keys() if k != "default"]
    return sorted(ks)

def india_adjust_cost(base: List[int], city: str) -> Tuple[int, int]:
    if not base or len(base) != 2:
        return (0, 0)
    m = KB["city_cost_modifiers"].get((city or "").strip().lower(), KB["city_cost_modifiers"]["default"])
    return (int(base[0]*m), int(base[1]*m))

def ascii_safe(s: str) -> str:
    if not s:
        return ""
    table = {"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", "•": "*", "…": "...", "₹": "Rs ", "\u00a0": " "}
    out = str(s)
    for k, v in table.items():
        out = out.replace(k, v)
    return out.encode("ascii", "replace").decode("ascii")

# -----------------------------
# Extraction
# -----------------------------
def extract_text_from_file(uploaded) -> Tuple[str, List[str]]:
    warnings = []
    name = uploaded.name.lower()
    data = uploaded.read()

    if name.endswith(".pdf"):
        try:
            text = ""
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for page in pdf.pages:
                    text += "\n" + (page.extract_text() or "")
            return text.strip(), warnings
        except Exception as e:
            return "", [f"PDF read error: {e}"]

    if name.endswith(".docx"):
        try:
            buf = io.BytesIO(data)
            return (docx2txt.process(buf) or ""), warnings
        except Exception as e:
            return "", [f"DOCX read error: {e}"]

    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        if OCR_AVAILABLE:
            return (pytesseract.image_to_string(im) or ""), warnings
        else:
            warnings.append("OCR not available. Install Tesseract+pytesseract.")
            return "", warnings
    except Exception:
        return "", ["Unsupported file. Upload PDF/DOCX/JPG/PNG."]

# -----------------------------
# Parsing & detection
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
        keywords = ["pain","lesion","fracture","mass","infection","infarct","tear","hernia","stone",
                    "blockage","tumor","ischemia","angina","colic"]
        guesses = [l for l in lines if any(w in l.lower() for w in keywords)]
        probs = list(dict.fromkeys(guesses[:3]))
    return probs

def word_hit(hay: str, needle: str) -> bool:
    if len(needle.strip()) < 3:
        return False
    return re.search(rf"\b{re.escape(needle.lower())}\b", hay) is not None

def detect_conditions(text: str) -> List[Dict]:
    t = normalize_text(text)
    results = []
    for key, meta in KB["conditions"].items():
        hits = [kw for kw in meta["keywords"] if word_hit(t, kw)]
        if hits:
            results.append({
                "key": key,
                "name": meta["display"],
                "hits": hits,
                "about": meta["about"],
                "actions": meta["actions"],
                "recovery": meta["recovery"],
                "specialist": meta["specialist"],
                "surgeries": meta["surgeries"],
                "cost_inr": meta["cost_inr"],
            })
    results.sort(key=lambda x: len(x["hits"]), reverse=True)
    return results

def severity_percent(text: str, cond: Dict) -> int:
    t = normalize_text(text)
    signals = ["severe","acute","sudden","worsening","emergency","fever","syncope","vomiting","bleeding",
               "dyspnea","chest pain","unstable","shock","collapse","sepsis","uncontrolled",
               "tachycardia","hypotension"]
    s = sum(1 for w in signals if w in t)
    hits_boost = min(5, len(cond.get("hits", [])))
    pct = s * 8 + hits_boost * 10
    if len(cond.get("hits", [])) > 0 and pct < 10:
        pct = 10
    pct = max(0, min(95, pct))
    if any(p in t for p in ["normal study", "within normal limits", "no acute", "normal chest x-ray", "normal chest xray"]):
        pct = min(pct, 5)
    return int(pct)

# -----------------------------
# Booking storage (CSV)
# -----------------------------
def load_bookings() -> pd.DataFrame:
    if os.path.exists(BOOKINGS_CSV):
        try:
            return pd.read_csv(BOOKINGS_CSV, dtype=str)
        except Exception:
            return pd.DataFrame(columns=["booking_id","patient_name","patient_phone","patient_email",
                                         "city","hospital","department","doctor","date","time"])
    else:
        return pd.DataFrame(columns=["booking_id","patient_name","patient_phone","patient_email",
                                     "city","hospital","department","doctor","date","time"])

def save_bookings(df: pd.DataFrame):
    df.to_csv(BOOKINGS_CSV, index=False)

def slot_taken(df: pd.DataFrame, hospital: str, doctor: str, dt_str: str, tm_str: str) -> bool:
    if df.empty: return False
    m = df[(df["hospital"]==hospital) & (df["doctor"]==doctor) & (df["date"]==dt_str) & (df["time"]==tm_str)]
    return not m.empty

def cancel_booking(booking_id: str):
    df = load_bookings()
    df2 = df[df["booking_id"] != booking_id]
    save_bookings(df2)

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
        pdf.multi_cell(0, 6, ascii_safe(f"Likely condition: {best['name']}"))
        pdf.multi_cell(0, 6, ascii_safe(f"Specialist: {best['specialist']}"))
        pdf.multi_cell(0, 6, ascii_safe(f"About: {best['about']}"))
        pdf.multi_cell(0, 6, ascii_safe(f"Typical surgery: {', '.join(best['surgeries'])}"))
        pdf.multi_cell(0, 6, ascii_safe(f"Immediate steps: {', '.join(best['actions'])}"))
        pdf.multi_cell(0, 6, ascii_safe(f"Recovery: {' | '.join(best['recovery'])}"))
        pdf.multi_cell(0, 6, ascii_safe(f"Severity: {best['severity_pct']}%"))
        pdf.multi_cell(0, 6, ascii_safe(f"Estimated cost (INR): Rs {best['cost_low']:,} – Rs {best['cost_high']:,}"))
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
# Email — flexible SMTP (host/port/security + fallback)
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
    if warns: st.warning("\n".join(warns))

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
            lo, hi = india_adjust_cost(c["cost_inr"], city)
            alts.append({**c, "severity_pct": sev_pct, "cost_low": lo, "cost_high": hi})
        alts.sort(key=lambda x: (len(x["hits"]), x["severity_pct"]), reverse=True)
        best = alts[0]
        st.session_state.alt_conditions = alts[1:]
    else:
        st.session_state.alt_conditions = []

    st.session_state.entities = ents
    st.session_state.problems = probs
    st.session_state.best_condition = best

    # Hospitals list (names only for display block)
    hospitals_list = [h["name"] for h in KB["hospitals"].get(city, KB["hospitals"]["default"])]
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
        pct = best["severity_pct"]
        sev_color = "#047857" if pct < 34 else ("#b45309" if pct < 67 else "#b91c1c")
        st.markdown(
            f'<div class="card"><span class="sev-tag" style="background:#f3f4f6;color:{sev_color};">Severity: {pct}%</span></div>',
            unsafe_allow_html=True
        )
        df_cond = pd.DataFrame([
            ["Likely condition", best["name"]],
            ["Specialist", best["specialist"]],
            ["About", best["about"]],
            ["What to do now", " ; ".join(best["actions"])],
            ["Recovery (typical)", " | ".join(best["recovery"])],
            ["Typical surgeries", ", ".join(best["surgeries"])],
            ["Estimated cost (INR)", f"₹{best['cost_low']:,} – ₹{best['cost_high']:,}"],
        ], columns=["Item", "Details"])
        st.table(df_cond)
    else:
        st.warning("No specific condition pattern matched. If your report says 'Normal', this can be expected. Otherwise, consult a clinician for personalized advice.")

    # ---------- 4) Hospitals ----------
    st.markdown('<div class="section-title">4) Suggested hospitals (India)</div>', unsafe_allow_html=True)
    st.dataframe(pd.DataFrame({"Hospitals": st.session_state.hospitals or ["—"]}), use_container_width=True)

    # ---------- 5) Appointment Booking System ----------
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">5) Hospital Appointment Booking</div>', unsafe_allow_html=True)

    hosp_dir = KB["hospitals"].get(city, KB["hospitals"]["default"])
    if not hosp_dir:
        st.info("Select a city with hospitals in the sidebar to enable booking.")
    else:
        # Choose hospital
        hospital_names = [h["name"] for h in hosp_dir]
        h_idx = st.selectbox("Choose a hospital", list(range(len(hospital_names))),
                             format_func=lambda i: hospital_names[i])
        chosen_h = hosp_dir[h_idx]
        hospital_name = chosen_h["name"]
        hospital_email = chosen_h.get("email","")

        # Choose department (suggest from condition)
        suggested_dept = None
        if best:
            # naive map specialist → department key
            spec = best["specialist"].lower()
            if "cardio" in spec: suggested_dept = "Cardiology"
            elif "urolog" in spec: suggested_dept = "Urology"
            elif "spine" in spec or "neuro" in spec: suggested_dept = "Spine/Neuro"
            elif "ent" in spec: suggested_dept = "ENT"
            elif "ophthal" in spec: suggested_dept = "Ophthalmology"
            elif "orthop" in spec: suggested_dept = "Orthopaedics"
            elif "general" in spec and "surgery" in spec: suggested_dept = "General Surgery"

        dept_names = list(chosen_h.get("departments", {}).keys())
        if suggested_dept in dept_names:
            dept_default = dept_names.index(suggested_dept)
        else:
            dept_default = 0 if dept_names else 0
        department = st.selectbox("Department", dept_names or ["General"], index=dept_default)
        doctors = chosen_h.get("departments", {}).get(department, ["Duty Doctor"])
        doctor = st.selectbox("Doctor", doctors)

        # Slot picker (next 14 days, 09:00–17:00, 30-min)
        df_book = load_bookings()
        colA, colB = st.columns(2)
        with colA:
            appt_date = st.date_input("Choose date", value=date.today(), min_value=date.today(), max_value=date.today()+timedelta(days=14))
        with colB:
            # build slot list
            slots = []
            start_dt = datetime.combine(appt_date, time(9, 0))
            end_dt = datetime.combine(appt_date, time(17, 0))
            cur = start_dt
            while cur < end_dt:
                slots.append(cur.strftime("%H:%M"))
                cur += timedelta(minutes=30)

            # remove taken
            free_slots = [s for s in slots if not slot_taken(df_book, hospital_name, doctor, str(appt_date), s)]
            appt_time = st.selectbox("Available time", free_slots or ["No slots available"])

        # Patient details
        colP1, colP2, colP3 = st.columns(3)
        with colP1:
            patient_name = st.text_input("Patient name", value=ents.get("Name",""))
        with colP2:
            patient_phone = st.text_input("Phone (optional)")
        with colP3:
            patient_email = st.text_input("Email (optional)")

        # Confirm booking
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
                df_new = pd.concat([df_book, pd.DataFrame([new_row])], ignore_index=True)
                save_bookings(df_new)

                st.success(f"Booked! ID: {booking_id}")
                # Build receipt PDF + ICS
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

    # ---------- 6) Full Report (original feature) ----------
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">6) Full report download</div>', unsafe_allow_html=True)

    appt_info = {"hospital": hospitals_list[0] if hospitals_list else "",
                 "date": "", "time": "", "phone": "", "email": ""}
    full_pdf = build_full_pdf(
        st.session_state.entities,
        st.session_state.problems,
        st.session_state.best_condition,
        city,
        st.session_state.hospitals,
        appt_info
    )
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
        # Try to use chosen hospital email if booking section was used
        fallback_email = ""
        if hosp_dir:
            fallback_email = hosp_dir[0].get("email","")
        hospital_email = st.text_input("Hospital / Recipient email", value=fallback_email)
        email_subject  = st.text_input("Email subject", value="Appointment Booking Request")

    with st.expander("Advanced SMTP settings"):
        smtp_host = st.text_input("SMTP host", value="smtp.gmail.com")
        smtp_port = st.number_input("SMTP port", min_value=1, max_value=65535, value=587, step=1)
        security = st.selectbox("Security", ["STARTTLS (recommended, port 587)", "SSL/TLS (port 465)"])
        smtp_timeout = st.number_input("Timeout (seconds)", min_value=5, max_value=120, value=25)

    # Compose email body
    body_lines = ["Dear Scheduling Team,", ""]
    if len(st.session_state.problems) > 0:
        body_lines.append("Report highlights: " + "; ".join(st.session_state.problems[:3]))
    if st.session_state.best_condition:
        body_lines.append(f"Possible condition (non-diagnostic): {st.session_state.best_condition['name']} "
                          f"| Severity: {st.session_state.best_condition['severity_pct']}%")
    body_lines.append("Please find attached a booking receipt (if I booked a slot) and my clinical summary.")
    body_lines += ["", "Thank you,", st.session_state.entities.get("Name","Patient") or "Patient"]
    email_body = "\n".join(body_lines)
    st.code(email_body)

    # Attachments: include receipt if exists, full report always, ICS if exists
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

# ---------- My Bookings (Persisted) ----------
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
st.markdown('<div class="section-title">My Bookings</div>', unsafe_allow_html=True)

df_all = load_bookings()
if df_all.empty:
    st.info("No bookings yet.")
else:
    st.dataframe(df_all, use_container_width=True)
    # Cancel controls
    cancel_id = st.text_input("Enter Booking ID to cancel")
    if st.button("Cancel Booking"):
        if cancel_id.strip():
            if cancel_id in set(df_all["booking_id"].astype(str)):
                cancel_booking(cancel_id.strip())
                st.success("Booking cancelled.")
            else:
                st.error("Booking ID not found.")
        else:
            st.error("Please enter a Booking ID.")

# Footer
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
st.caption("© 2025 — For education/information only. Not a medical device; not medical advice.")
