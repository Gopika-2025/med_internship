# app.py
# -------------------------------------------------------------------
# Clinical Report Helper (Educational, India) — Auto-flow, Email & Full PDF
# - Upload (PDF/DOCX/Image) → instant tables (no button)
# - Extract Name/Age/Sex/Problems
# - Detect likely condition (rule-based, India pack)
# - Show Description, What to do, Recovery, Severity, Cost (INR by city)
# - India-only city list & hospitals
# - Full PDF report (Issue, Surgery, Recovery, Cost, Appointment details)
# - Appointment email (to hospital) + optional BCC to you, with PDF & ICS attached
# IMPORTANT: Not a medical device; Not medical advice.
# -------------------------------------------------------------------

import streamlit as st
import io, re, json, uuid
from typing import Dict, List, Tuple, Any
from datetime import datetime, date, time

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
.sev-low {background:#ecfdf5;color:#065f46;}
.sev-med {background:#fff7ed;color:#9a3412;}
.sev-high{background:#fee2e2;color:#991b1b;}
.hr {height:1px;background:#e5e7eb;border:none;margin:16px 0;}
</style>
""", unsafe_allow_html=True)

# -----------------------------
# Session defaults
# -----------------------------
for k, v in {
    "extracted_text": "",
    "entities": {},
    "problems": [],
    "best_condition": None,
    "alt_conditions": [],
    "hospitals": [],
    "email_draft": "",
    "latest_pdf_bytes": b"",
    "latest_ics_bytes": b"",
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# -----------------------------
# India KB (condensed)
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
    "hospitals": {
        "mumbai": ["Kokilaben Hospital", "Nanavati Max", "Jaslok Hospital"],
        "delhi": ["AIIMS (consults vary)", "Max Saket", "Fortis Escorts Okhla"],
        "chennai": ["Apollo Greams Road", "Fortis Malar", "MIOT International"],
        "bengaluru": ["Manipal Old Airport Road", "Aster CMI", "Fortis Bannerghatta"],
        "hyderabad": ["AIG Hospitals", "Yashoda Somajiguda", "KIMS"],
        "pune": ["Jehangir Hospital", "Ruby Hall Clinic", "Sahyadri"],
        "kolkata": ["Apollo Gleneagles", "AMRI", "Fortis Anandapur"],
        "ahmedabad": ["CIMS Hospital", "Sterling", "HCG Ahmedabad"],
        "jaipur": ["EHCC", "SMS Hospital (consults)", "Fortis Jaipur"],
        "kochi": ["AIMS Kochi", "Lakeshore", "Rajagiri"],
        "coimbatore": ["PSG Hospitals", "KMCH", "GKNM"],
        "madurai": ["Meenakshi Mission", "Velammal Medical College", "Apollo Specialty"],
        "trichy": ["Kauvery Trichy", "Apollo Trichy", "SRM Trichy"],
        "karur": ["KMC Karur", "Kauvery Karur", "Government HQ Hospital"],
        "surat": ["Sunshine Global", "Unique Hospital", "Svani"],
        "indore": ["CHL Hospital", "Bombay Hospital Indore", "Choithram"],
        "lucknow": ["Sanjay Gandhi PGI (consults)", "Medanta", "Apollo Medics"],
        "chandigarh": ["PGIMER (consults)", "Fortis Mohali", "Max Chandigarh"],
        "nagpur": ["Wockhardt Nagpur", "Care Nagpur", "Alexis"],
        "bhubaneswar": ["AMRI Bhubaneswar", "SUM Ultimate", "Apollo Bhubaneswar"],
        "default": ["Accredited tertiary center near you"]
    },
    "conditions": {
        "appendicitis": {
            "display": "Acute Appendicitis",
            "keywords": ["appendicitis","appendix","rlq pain","mcburney","appendectomy"],
            "about": "Inflammation of the appendix causing right lower abdominal pain and fever.",
            "actions": ["Urgent surgical evaluation","IV fluids & antibiotics per clinician","Nil by mouth if surgery planned"],
            "recovery": ["Discharge ~24–48h after lap surgery","Light activity in a few days","Avoid heavy lifting 2–4 weeks"],
            "red_flags": ["Severe/worsening abdominal pain with fever/vomiting","Rigid abdomen/peritonitis"],
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
            "red_flags": ["High fever, jaundice, severe RUQ pain","Dark urine/pale stools"],
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
            "red_flags": ["Irreducible painful bulge, vomiting (strangulation)"],
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
            "red_flags": ["Fever with obstruction (pyonephrosis)"],
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
            "red_flags": ["Severe bleeding, syncope, fever post-op"],
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
            "red_flags": ["Severe swelling, inability to bear weight with fever"],
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
            "red_flags": ["Bowel/bladder dysfunction, saddle anesthesia, progressive weakness"],
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
            "red_flags": ["Chest pain at rest, dyspnea, diaphoresis","Syncope/arrhythmia"],
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
            "red_flags": ["Profuse bleeding, orbital swelling/vision changes"],
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
            "red_flags": ["Severe eye pain/sudden vision loss post-op"],
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
            "red_flags": ["Infection, uncontrolled bleeding, lymphedema"],
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
    if not base or len(base) != 2: return (0, 0)
    m = KB["city_cost_modifiers"].get((city or "").strip().lower(), KB["city_cost_modifiers"]["default"])
    return (int(base[0]*m), int(base[1]*m))

def nearby_hospitals(city: str) -> List[str]:
    return KB["hospitals"].get((city or "").strip().lower(), KB["hospitals"]["default"])

# --- PDF-safe text helper (fixes download issues with smart punctuation) ---
def ascii_safe(s: str) -> str:
    if not s:
        return ""
    table = {
        "’": "'", "‘": "'", "“": '"', "”": '"',
        "–": "-", "—": "-", "•": "*", "…": "...", "₹": "Rs ",
        "\u00a0": " ",
    }
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

    # Images → OCR
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
    if len(needle.strip()) < 3: return False
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
                "red_flags": meta["red_flags"],
                "specialist": meta["specialist"],
                "surgeries": meta["surgeries"],
                "cost_inr": meta["cost_inr"],
            })
    results.sort(key=lambda x: len(x["hits"]), reverse=True)
    return results

def score_severity(text: str, cond: Dict) -> Tuple[str, int]:
    t = normalize_text(text)
    score = 0
    for rf in cond.get("red_flags", []):
        for token in re.findall(r"[a-zA-Z]{4,}", rf.lower()):
            if token and token in t:
                score += 2
    for w in ["severe","acute","sudden","worsening","emergency","fever","syncope","vomiting","bleeding","dyspnea","chest pain","uncontrolled"]:
        if w in t: score += 1
    if score >= 6:   return ("High", score)
    if score >= 3:   return ("Medium", score)
    return ("Low", score)

# -----------------------------
# PDF builders (safe-encoding)
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
        pdf.multi_cell(0, 6, ascii_safe(f"Severity: {best['severity']}"))
        pdf.multi_cell(0, 6, ascii_safe(f"Estimated cost (INR): Rs {best['cost_low']:,} – Rs {best['cost_high']:,}"))
        if best.get("red_flags"):
            pdf.multi_cell(0, 6, ascii_safe("Red flags:"))
            for rf in best["red_flags"]:
                pdf.multi_cell(0, 6, ascii_safe(f"  - {rf}"))
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
    pdf.multi_cell(0, 6, ascii_safe("Informational only — NOT a medical diagnosis. Consult a qualified clinician. "
                                     "If red-flag symptoms are present, seek urgent care."))

    return pdf.output(dest="S").encode("latin-1", "replace")

# -----------------------------
# ICS builder (30 min slot)
# -----------------------------
def build_ics(patient_name: str, city: str, hospital: str, specialist: str,
              appt_dt: datetime) -> bytes:
    end_dt = appt_dt.replace(minute=(appt_dt.minute + 30) % 60, hour=appt_dt.hour + ((appt_dt.minute + 30)//60))
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
# Email sender (with attachments + optional BCC self)
# -----------------------------
def send_email_with_attachments(sender_email: str, sender_password: str,
                                to_email: str, subject: str, body: str,
                                attachments: List[Tuple[bytes, str, str]],  # (data, filename, mime)
                                bcc_self: bool=False) -> Tuple[bool, str]:
    try:
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

        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
        return True, "Email sent."
    except Exception as e:
        return False, f"Email failed: {e}"

# -----------------------------
# Sidebar — India city
# -----------------------------
st.sidebar.header("India Location")
cities = [""] + list_india_cities()
city = st.sidebar.selectbox("Choose your city (India)", cities, index=(cities.index("chennai") if "chennai" in cities else 0))
st.sidebar.caption("Used for cost estimates & hospital suggestions in India.")

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

# Auto-run once uploaded
if uploaded is not None:
    text, warns = extract_text_from_file(uploaded)
    st.session_state.extracted_text = text
    if warns: st.warning("\n".join(warns))

if st.session_state.extracted_text:
    # Parse
    ents = parse_entities(st.session_state.extracted_text)
    probs = summarize_problems(st.session_state.extracted_text)

    # Normal-study guard
    tn = normalize_text(st.session_state.extracted_text)
    normal_markers = [
        "normal chest x-ray", "normal chest xray", "normal chest x-ray study",
        "no acute cardiopulmonary", "no focal consolidation", "no pleural effusion",
        "within normal limits", "impression: normal", "impression : normal", "normal study"
    ]
    detected = [] if any(p in tn for p in normal_markers) else detect_conditions(st.session_state.extracted_text)

    # Choose best + compute severity + INR cost by city
    best = None
    alts = []
    if detected:
        for c in detected:
            sev, sev_score = score_severity(st.session_state.extracted_text, c)
            lo, hi = india_adjust_cost(c["cost_inr"], city)
            alts.append({**c, "severity": sev, "severity_score": sev_score,
                         "cost_low": lo, "cost_high": hi})
        alts.sort(key=lambda x: (len(x["hits"]), x["severity_score"]), reverse=True)
        best = alts[0]
        st.session_state.alt_conditions = alts[1:]
    else:
        st.session_state.alt_conditions = []

    st.session_state.entities = ents
    st.session_state.problems = probs
    st.session_state.best_condition = best
    st.session_state.hospitals = nearby_hospitals(city)

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
        sev_class = "sev-low" if best["severity"]=="Low" else ("sev-med" if best["severity"]=="Medium" else "sev-high")
        st.markdown(f'<div class="card"><span class="big-badge {sev_class}">Severity: {best["severity"]}</span></div>', unsafe_allow_html=True)

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

        if best.get("red_flags"):
            st.info("Red flags: " + " | ".join(best["red_flags"]))
    else:
        st.warning("No specific condition pattern matched. If your report says 'Normal', this can be expected. Otherwise, consult a clinician for personalized advice.")

    # ---------- 4) Hospitals ----------
    st.markdown('<div class="section-title">4) Suggested hospitals (India)</div>', unsafe_allow_html=True)
    st.dataframe(pd.DataFrame({"Hospitals": st.session_state.hospitals or ["—"]}), use_container_width=True)

    # ---------- 5) Appointment helper ----------
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">5) Appointment helper</div>', unsafe_allow_html=True)
    colA, colB = st.columns(2)
    with colA:
        patient_name = st.text_input("Your name (for email)", value=ents.get("Name",""))
        appt_date = st.date_input("Preferred date", value=date.today())
        appt_time = st.time_input("Preferred time", value=time(10,0))
    with colB:
        hospital = st.selectbox("Choose a hospital", st.session_state.hospitals or ["—"])
        patient_phone = st.text_input("Phone (optional)")
        patient_email = st.text_input("Email (optional)")

    spec = best["specialist"] if best else "Relevant Specialist"
    email_lines = [
        f"Subject: Appointment Request — {spec}",
        "",
        "Dear Scheduling Team,",
        "",
        f"My name is {patient_name or 'Patient'}. I’d like to book an appointment in {city or 'my city'} at {hospital or 'your hospital'} with a {spec}.",
        "",
        "Summary:"
    ]
    if probs: email_lines.append(f"• Report highlights: {'; '.join(probs[:3])}")
    if best:  email_lines.append(f"• Possible condition (non-diagnostic): {best['name']}")
    email_lines.append(f"• Preferred slot: {appt_date} at {appt_time}")
    if patient_phone: email_lines.append(f"• Phone: {patient_phone}")
    if patient_email: email_lines.append(f"• Email: {patient_email}")
    email_lines += ["", "I can share my report on request.", "", "Thank you,", f"{patient_name or 'Patient'}"]
    booking_email = "\n".join(email_lines)
    st.code(booking_email)

    # Prepare appointment artifacts (for download + email attach)
    appt_dt = datetime.combine(appt_date, appt_time)
    ics_bytes = build_ics(patient_name, city, hospital, spec, appt_dt)
    st.session_state.latest_ics_bytes = ics_bytes

    # ---------- 6) Full report download ----------
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">6) Full report download</div>', unsafe_allow_html=True)

    appt_info = {
        "hospital": hospital,
        "date": str(appt_date),
        "time": str(appt_time),
        "phone": patient_phone,
        "email": patient_email
    }
    full_pdf = build_full_pdf(ents, probs, best, city, st.session_state.hospitals, appt_info)
    st.session_state.latest_pdf_bytes = full_pdf

    st.download_button("⬇️ Download Full Report (PDF)",
                       data=full_pdf, file_name="clinical_full_report.pdf", mime="application/pdf")
    st.download_button("⬇️ Download Calendar (.ics)",
                       data=ics_bytes, file_name="appointment.ics", mime="text/calendar")

    # ---------- Email booking (to hospital) + copy to you ----------
    st.markdown('<div class="section-title" style="margin-top:10px;">Email booking</div>', unsafe_allow_html=True)
    colE1, colE2 = st.columns(2)
    with colE1:
        sender_email = st.text_input("Your email (SMTP user) e.g. Gmail")
        sender_pass  = st.text_input("App password / SMTP password", type="password")
        send_copy_to_me = st.checkbox("Send me a copy (BCC)", value=True)
    with colE2:
        hospital_email = st.text_input("Hospital / Recipient email")
        email_subject  = st.text_input("Email subject", value=f"Appointment Request — {spec}")

    if st.button("📧 Book & Email (with PDF + ICS attached)"):
        if not (sender_email and sender_pass and hospital_email):
            st.error("Please fill: Your email, password, and Hospital email.")
        else:
            attachments = [
                (full_pdf, "clinical_full_report.pdf", "application/pdf"),
                (ics_bytes, "appointment.ics", "text/calendar")
            ]
            ok, msg = send_email_with_attachments(
                sender_email=sender_email,
                sender_password=sender_pass,
                to_email=hospital_email,
                subject=email_subject,
                body=booking_email,
                attachments=attachments,
                bcc_self=send_copy_to_me
            )
            st.success(msg) if ok else st.error(msg)

# Footer
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
st.caption("© 2025 — For education/information only. Not a medical device; not medical advice.")
