# app.py
# -------------------------------------------------------------------
# Clinical Report Helper (Educational) — Streamlit App
# - Upload diagnosis reports (PDF/DOCX/Images)
# - Extract text (pdfplumber, docx2txt, OCR via Tesseract)
# - Smart, structured summary (Patient Info, Findings, Impression, etc.)
# - Rule-based condition suggestions, red-flag detector, rough cost ranges
# - Appointment email helper + optional SMTP send
# - Download JSON/PDF + CSVs for tables
# IMPORTANT: Not a substitute for professional medical advice/diagnosis.
# -------------------------------------------------------------------

import streamlit as st
import io
import re
import json
from datetime import datetime
from typing import Dict, List, Tuple, Any

import pandas as pd

# File handlers
import pdfplumber
import docx2txt
from PIL import Image

# Optional OCR (requires Tesseract installed on system)
try:
    import pytesseract
    OCR_AVAILABLE = True
except Exception:
    OCR_AVAILABLE = False

# PDF generation
from fpdf import FPDF

# Optional email
import smtplib
import ssl
from email.message import EmailMessage


st.set_page_config(
    page_title="Clinical Report Helper (Educational)",
    page_icon="🩺",
    layout="centered"
)

# ----- Light CSS polish (badges, subtle cards) -----
st.markdown("""
<style>
.badge {display:inline-block;padding:3px 8px;border-radius:999px;font-size:12px;background:#EEF2FF;color:#3730A3;margin-right:6px;}
.badge-red {background:#FEE2E2;color:#991B1B;}
.badge-green {background:#DCFCE7;color:#166534;}
.card {border:1px solid #e6e6e6;border-radius:12px;padding:14px;margin-top:8px;}
.small-muted {color:#6b7280;font-size:12px;}
.hr {height:1px;background:#eee;border:none;margin:14px 0;}
</style>
""", unsafe_allow_html=True)

# -----------------------------
# Session defaults
# -----------------------------
for key, default in {
    "problems": [],
    "conditions": [],
    "hospitals": [],
    "email_draft": "",
    "extracted_text": "",
    "sections": {},
    "entities": {},
}.items():
    if key not in st.session_state:
        st.session_state[key] = default

# -----------------------------
# Knowledge base (starter)
# -----------------------------
KB = {
    "conditions": {
        "appendicitis": {
            "keywords": ["appendicitis", "appendix inflammation", "rlq pain", "mcburney"],
            "specialist": "General Surgeon",
            "surgeries": ["Laparoscopic appendectomy", "Open appendectomy (rare)"],
            "recovery": [
                "Usually discharge within 24–48h after laparoscopic surgery",
                "Light activity after a few days; avoid heavy lifting ~2–4 weeks",
                "Follow wound care instructions; monitor for fever/redness/worsening pain"
            ],
            "cost_ranges_usd": {"default": [6000, 18000]},
            "red_flags": [
                "Worsening severe abdominal pain with fever/vomiting",
                "Rigid abdomen / peritonitis"
            ]
        },
        "cholelithiasis": {
            "keywords": ["cholelithiasis", "gallstones", "biliary colic", "cholecystitis"],
            "specialist": "General Surgeon",
            "surgeries": ["Laparoscopic cholecystectomy"],
            "recovery": [
                "Often same-day or next-day discharge",
                "Return to desk work ~1 week; strenuous activity 2–4 weeks",
                "Low-fat diet initially if advised"
            ],
            "cost_ranges_usd": {"default": [7000, 20000]},
            "red_flags": [
                "High fever, jaundice, severe RUQ pain",
                "Dark urine / pale stools"
            ]
        },
        "lumbar_disc_herniation": {
            "keywords": ["lumbar disc herniation", "l4-l5", "l5-s1", "sciatica", "radiculopathy"],
            "specialist": "Orthopedic Spine / Neurosurgeon",
            "surgeries": ["Microdiscectomy (if indicated)"],
            "recovery": [
                "Many improve with conservative care (physiotherapy/analgesia)",
                "If surgery: early mobilization; avoid heavy lifting ~4–6 weeks",
                "Core strengthening per physiotherapist"
            ],
            "cost_ranges_usd": {"default": [8000, 25000]},
            "red_flags": [
                "Bowel/bladder dysfunction, saddle anesthesia, progressive weakness"
            ]
        },
        "cad": {
            "display": "Coronary Artery Disease",
            "keywords": ["coronary artery disease", "cad", "angina", "nstemi", "stemi", "mi", "myocardial infarction"],
            "specialist": "Cardiologist",
            "surgeries": ["PCI (angioplasty + stent)", "CABG (bypass) — selected cases"],
            "recovery": [
                "Cardiac rehab strongly recommended",
                "Medication adherence (as prescribed)",
                "Lifestyle: smoking cessation, diet, exercise (with clearance)"
            ],
            "cost_ranges_usd": {"pci": [12000, 30000], "cabg": [35000, 100000], "default": [12000, 100000]},
            "red_flags": [
                "Chest pain at rest, dyspnea, diaphoresis",
                "Syncope, arrhythmia, hemodynamic instability"
            ]
        }
    },
    "cost_modifiers": {
        "india": 0.25, "europe": 1.0, "usa": 1.2, "uk": 1.1,
        "south_africa": 0.6, "brazil": 0.5, "uae": 1.0, "default": 1.0
    },
    "hospitals": {
        "chennai": ["Apollo Hospitals Greams Road", "Fortis Malar Hospital", "MIOT International"],
        "coimbatore": ["PSG Hospitals", "KG Hospital", "G Kuppuswamy Naidu Memorial"],
        "bengaluru": ["Manipal Hospitals Old Airport Road", "Aster CMI", "Fortis Bannerghatta"],
        "new york": ["NYU Langone", "Mount Sinai", "NewYork-Presbyterian"],
        "london": ["St Thomas’ Hospital", "Royal London Hospital", "UCLH"],
        "default": ["Local tertiary care hospital", "Accredited specialty center"]
    }
}

# -----------------------------
# Helpers (parsing & analysis)
# -----------------------------
def normalize_text(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip().lower()

def extract_text_from_file(uploaded) -> Tuple[str, List[str]]:
    """Return (text, warnings)."""
    warnings = []
    if uploaded is None:
        return "", warnings

    name = uploaded.name.lower()
    data = uploaded.read()

    # PDF
    if name.endswith(".pdf"):
        try:
            text = ""
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for page in pdf.pages:
                    txt = page.extract_text() or ""
                    text += "\n" + txt
            text = text.strip()
            if not text and OCR_AVAILABLE:
                warnings.append("Embedded text not found; OCR for PDFs not implemented in this starter.")
            return text, warnings
        except Exception as e:
            warnings.append(f"PDF read error: {e}")
            return "", warnings

    # DOCX
    if name.endswith(".docx"):
        try:
            buf = io.BytesIO(data)
            text = docx2txt.process(buf)
            return text or "", warnings
        except Exception as e:
            warnings.append(f"DOCX read error: {e}")
            return "", warnings

    # Images (JPG/PNG)
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        if OCR_AVAILABLE:
            text = pytesseract.image_to_string(img)
            return text or "", warnings
        else:
            warnings.append("OCR not available. Install Tesseract and pytesseract.")
            return "", warnings
    except Exception:
        warnings.append("Unsupported file type. Upload PDF/DOCX/JPG/PNG.")
        return "", warnings

def parse_sections(text: str) -> Dict[str, str]:
    """
    Build a structured outline by common clinical headings.
    Uses forgiving regex to match variants.
    """
    if not text:
        return {}
    lines = text.splitlines()
    joined = "\n".join(lines)

    def grab(head: str, stop_heads: List[str]) -> str:
        # Capture from head to next stop head (case-insensitive)
        pattern = rf"(?im)^{head}\s*[:\-]?\s*(.+?)(?=^(?:{'|'.join(stop_heads)})\s*:?\s*|$\Z)"
        m = re.search(pattern, joined, flags=re.DOTALL | re.MULTILINE | re.IGNORECASE)
        return (m.group(1).strip() if m else "")

    # Common headings
    heads = {
        "Patient": ["patient", "patient details", "demographics"],
        "History": ["history", "clinical history", "presenting complaint", "HPI"],
        "Findings": ["findings", "examination", "study findings", "observations"],
        "Impression": ["impression", "conclusion", "opinion", "diagnosis"],
        "Recommendations": ["recommendations", "plan", "advice", "follow up"],
        "Medications": ["medications", "medicines", "current medications"],
        "Labs": ["labs", "laboratory", "investigations", "test results"],
        "Vitals": ["vitals", "vital signs"],
    }

    # Flatten stop heads
    all_heads = sorted({h for arr in heads.values() for h in arr}, key=len, reverse=True)
    sections: Dict[str, str] = {}
    for title, variants in heads.items():
        content = ""
        for v in variants:
            stop = [x for x in all_heads if x.lower() != v.lower()]
            content = grab(v, stop)
            if content:
                break
        sections[title] = content

    return sections

def extract_entities(text: str) -> Dict[str, Any]:
    """
    Light entity extractor (regex heuristics):
    - Patient name, age, sex
    - Medications (simple list by keywords/patterns)
    - Labs (value + unit style)
    - Vitals (BP, HR, Temp, RR, SpO2)
    """
    ents: Dict[str, Any] = {}

    # Name (very heuristic)
    m = re.search(r"(?i)\b(name|patient name)\s*[:\-]\s*([A-Za-z ,.'-]+)", text)
    ents["Name"] = (m.group(2).strip() if m else "")

    # Age
    m = re.search(r"(?i)\b(age)\s*[:\-]\s*(\d{1,3})", text)
    ents["Age"] = (m.group(2) if m else "")

    # Sex
    m = re.search(r"(?i)\b(sex|gender)\s*[:\-]\s*(male|female|m|f|other)", text)
    ents["Sex"] = (m.group(2).capitalize() if m else "")

    # Simple meds (look for generic patterns)
    med_lines = []
    for line in text.splitlines():
        if re.search(r"(?i)\bmg\b|\btablet\b|\btab\.\b|\bdose\b|\bcap(s)?\b|\bsyrup\b|\binjection\b", line):
            med_lines.append(line.strip())
    ents["Medication lines"] = list(dict.fromkeys(med_lines[:10]))

    # Labs (value + unit)
    labs = re.findall(r"(?i)([A-Za-z ]{2,20})\s*[:\-]?\s*([0-9]+(?:\.[0-9]+)?)\s*(mg/dl|g/dl|mmol/l|iu/l|u/l|%|ng/ml|pg/ml|mmhg)", text)
    ents["Labs parsed"] = [{"Test": t.strip(), "Value": v, "Unit": u.upper()} for (t, v, u) in labs][:20]

    # Vitals
    vitals = {}
    m = re.search(r"(?i)\bBP\b\s*[:\-]?\s*(\d{2,3}\s*\/\s*\d{2,3})", text)
    if m: vitals["BP"] = m.group(1).replace(" ", "")
    m = re.search(r"(?i)\bHR\b|\bPulse\b\s*[:\-]?\s*(\d{2,3})\s*bpm?", text)
    if m: vitals["HR"] = re.sub(r"(?i)\s*bpm", "", m.group(0)).split()[-1]
    m = re.search(r"(?i)\bTemp(?:erature)?\b\s*[:\-]?\s*([0-9]{2}(?:\.[0-9])?)\s*(°?C|°?F)", text)
    if m: vitals["Temp"] = f"{m.group(1)} {m.group(2)}"
    m = re.search(r"(?i)\bRR\b\s*[:\-]?\s*(\d{1,2})", text)
    if m: vitals["RR"] = m.group(1)
    m = re.search(r"(?i)\bSpO2\b\s*[:\-]?\s*(\d{2,3})\s*%", text)
    if m: vitals["SpO2"] = f"{m.group(1)}%"

    ents["Vitals parsed"] = vitals
    return ents

def detect_conditions(text: str) -> List[Dict]:
    """Naive rule-based detection based on KB keywords."""
    found = []
    tnorm = normalize_text(text)
    for key, meta in KB["conditions"].items():
        hits = [kw for kw in meta["keywords"] if kw in tnorm]
        if hits:
            found.append({
                "key": key,
                "name": meta.get("display", key.replace("_", " ").title()),
                "hits": hits,
                "specialist": meta["specialist"],
                "surgeries": meta["surgeries"],
                "recovery": meta["recovery"],
                "red_flags": meta["red_flags"],
                "cost_ranges_usd": meta["cost_ranges_usd"]
            })
    return found

def summarize_problems(text: str) -> List[str]:
    problems = []
    patterns = [
        r"(?i)impression[:\-]\s*(.+)",
        r"(?i)diagnosis[:\-]\s*(.+)",
        r"(?i)conclusion[:\-]\s*(.+)",
        r"(?i)findings[:\-]\s*(.+)"
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            chunk = m.group(1).strip()
            if chunk and chunk not in problems:
                problems.append(chunk[:300])
    if not problems:
        lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 0]
        keywords = ["pain", "lesion", "fracture", "mass", "infection", "infarct", "tear", "hernia", "stone", "blockage", "tumor", "ischemia", "colic", "angina"]
        guesses = [l for l in lines if any(w in l.lower() for w in keywords)]
        problems = list(dict.fromkeys(guesses[:3]))
    return problems

def pick_cost_range(meta_cost: Dict, location_key: str) -> Tuple[int, int]:
    base_low, base_high = meta_cost.get("default", [0, 0])
    mod = KB["cost_modifiers"].get(location_key, KB["cost_modifiers"]["default"])
    return int(base_low * mod), int(base_high * mod)

def infer_region_key(user_region: str) -> str:
    if not user_region:
        return "default"
    r = user_region.strip().lower()
    if any(x in r for x in ["usa", "united states", "new york", "california"]): return "usa"
    if any(x in r for x in ["india", "chennai", "coimbatore", "bengaluru", "mumbai", "delhi"]): return "india"
    if any(x in r for x in ["uk", "united kingdom", "london", "manchester"]): return "uk"
    if any(x in r for x in ["europe", "france", "germany", "spain", "italy", "netherlands"]): return "europe"
    if any(x in r for x in ["south africa", "johannesburg", "cape town", "durban"]): return "south_africa"
    if any(x in r for x in ["brazil", "são paulo", "rio de janeiro"]): return "brazil"
    if any(x in r for x in ["uae", "dubai", "abudhabi", "abu dhabi"]): return "uae"
    return "default"

def nearby_hospitals(city: str) -> List[str]:
    if not city: return KB["hospitals"]["default"]
    c = city.strip().lower()
    return KB["hospitals"].get(c, KB["hospitals"]["default"])

# -----------------------------
# PDF builder
# -----------------------------
def make_pdf(summary: Dict) -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()
    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "Clinical Report Helper (Educational Summary)", ln=True)
    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 7, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True)

    def write_section(title):
        pdf.set_font("Arial", "B", 12)
        pdf.ln(3); pdf.cell(0, 8, title, ln=True)
        pdf.set_font("Arial", "", 11)

    # Patient info (entities)
    write_section("Patient Information (heuristic)")
    ents = summary.get("entities", {})
    pdf.multi_cell(0, 6, f"Name: {ents.get('Name', '') or '—'}")
    pdf.multi_cell(0, 6, f"Age/Sex: {ents.get('Age','') or '—'} / {ents.get('Sex','') or '—'}")

    write_section("Detected Problems")
    if summary["problems"]:
        for p in summary["problems"]: pdf.multi_cell(0, 6, f"• {p}")
    else:
        pdf.multi_cell(0, 6, "None detected from text. (Manual review advised.)")

    write_section("Possible Conditions (non-diagnostic)")
    if summary["conditions"]:
        for c in summary["conditions"]:
            pdf.multi_cell(0, 6, f"- {c['name']} (Specialist: {c['specialist']})")
            if c.get("surgeries"): pdf.multi_cell(0, 6, f"  Surgeries: {', '.join(c['surgeries'])}")
            if c.get("cost_estimate"):
                lo, hi = c["cost_estimate"]; pdf.multi_cell(0, 6, f"  Cost (rough): ${lo:,} – ${hi:,}")
            if c.get("recovery"):
                for r in c["recovery"]: pdf.multi_cell(0, 6, f"  ▹ {r}")
            if c.get("red_flags"):
                pdf.multi_cell(0, 6, "  Red flags:")
                for rf in c["red_flags"]: pdf.multi_cell(0, 6, f"    - {rf}")
            pdf.ln(1)
    else:
        pdf.multi_cell(0, 6, "No condition patterns matched.")

    write_section("Suggested Hospitals")
    for h in summary["hospitals"]: pdf.multi_cell(0, 6, f"• {h}")

    write_section("Appointment Email Draft")
    pdf.multi_cell(0, 6, summary["appointment_email"])

    write_section("Disclaimer")
    pdf.multi_cell(0, 6,
        "Informational only — NOT a medical diagnosis. Consult a qualified clinician. "
        "If red-flag symptoms are present, seek urgent care."
    )

    return pdf.output(dest="S").encode("latin-1")

# -----------------------------
# Email helper
# -----------------------------
def build_email_draft(patient_name: str, city: str, specialist: str,
                      problems: List[str], conditions: List[Dict], preferred_time: str) -> str:
    cond_line = ", ".join([c["name"] for c in conditions]) if conditions else "—"
    problem_line = "; ".join(problems) if problems else "—"
    lines = [
        f"Subject: Appointment Request — {specialist or 'Relevant Specialist'}",
        "",
        "Dear Scheduling Team,",
        "",
        f"My name is {patient_name or 'Patient'}. I’m seeking an appointment in {city or 'my city'} with a {specialist or 'relevant specialist'}.",
        "",
        "Summary:",
        f"• Report highlights / problems: {problem_line}",
        f"• Possible conditions (non-diagnostic): {cond_line}",
        f"• Preferred time: {preferred_time or 'Next available'}",
        "",
        "If you require my report, I can share it securely.",
        "",
        "Thank you,",
        f"{patient_name or 'Patient'}"
    ]
    return "\n".join(lines)

def safe_send_email(sender_email: str, sender_password: str, receiver_email: str,
                    subject: str, body: str) -> Tuple[bool, str]:
    try:
        em = EmailMessage()
        em["Subject"] = subject
        em["From"] = sender_email
        em["To"] = receiver_email
        em.set_content(body)
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(sender_email, sender_password)
            server.send_message(em)
        return True, "Email sent."
    except Exception as e:
        return False, f"Email failed: {e}"

# -----------------------------
# UI
# -----------------------------
st.title("🩺 Clinical Report Helper (Educational)")
st.caption("Analyze a diagnosis report → get an educational summary → prep an appointment request. **Not medical advice.**")

with st.expander("Read me first (Safety & Privacy)", expanded=False):
    st.markdown("""
- This tool **does not** provide a medical diagnosis. It summarizes text and shows general options.
- If you have **red-flag** symptoms (severe/worsening pain, chest pain, breathing difficulty, etc.), seek urgent care immediately.
- Do not paste secrets. If you email from the app, credentials are used only in your current session.
    """)

# 1) Upload report
st.header("1) Upload your report")
uploaded = st.file_uploader("Upload PDF, DOCX, or a clear image (JPG/PNG)", type=["pdf", "docx", "jpg", "jpeg", "png"])

if uploaded:
    extracted_text, warnings = extract_text_from_file(uploaded)
    st.session_state.extracted_text = extracted_text
    if warnings: st.warning("\n".join(warnings))
    if extracted_text:
        with st.expander("Preview extracted text"):
            st.text_area("Extracted text", extracted_text, height=220)
    else:
        st.error("Could not extract text. Try another file or enable OCR (Tesseract).")

# 2) Analysis inputs
st.header("2) Analysis")
user_location = st.text_input("Your region/country (e.g., 'India', 'USA', 'UK', 'South Africa')", help="Used for rough cost ranges.")
city = st.text_input("Preferred city for appointment (e.g., 'Chennai', 'London', 'New York')")

# Run analysis
if st.button("Run analysis", disabled=(not st.session_state.extracted_text)):
    if not st.session_state.extracted_text:
        st.error("Please upload a report first.")
    else:
        # Structured outline & entities (unique touch)
        sections = parse_sections(st.session_state.extracted_text)
        entities = extract_entities(st.session_state.extracted_text)

        problems = summarize_problems(st.session_state.extracted_text)
        detected = detect_conditions(st.session_state.extracted_text)

        region_key = infer_region_key(user_location)
        enriched = []
        for c in detected:
            lo, hi = pick_cost_range(c["cost_ranges_usd"], region_key)
            c["cost_estimate"] = (lo, hi)
            enriched.append(c)

        st.session_state.sections = sections
        st.session_state.entities = entities
        st.session_state.problems = problems
        st.session_state.conditions = enriched
        st.session_state.hospitals = nearby_hospitals(city)

# Display results
if st.session_state.extracted_text:
    # Urgency banner: if any detected condition has red flags
    has_red_flags = any(c.get("red_flags") for c in st.session_state.conditions)
    if has_red_flags:
        st.markdown('<div class="card"><span class="badge badge-red">Red-flag check</span> '
                    'If you have severe/worsening symptoms listed below, seek urgent care.</div>', unsafe_allow_html=True)

    st.subheader("Structured summary")
    colA, colB = st.columns(2)
    with colA:
        # Patient info (entities)
        ents = st.session_state.entities
        st.markdown('<div class="card"><span class="badge">Patient</span>', unsafe_allow_html=True)
        info_rows = {
            "Name": ents.get("Name", "") or "—",
            "Age": ents.get("Age", "") or "—",
            "Sex": ents.get("Sex", "") or "—"
        }
        st.table(pd.DataFrame(list(info_rows.items()), columns=["Field", "Value"]))
        st.markdown('</div>', unsafe_allow_html=True)

        # Findings / Impression
        st.markdown('<div class="card"><span class="badge">Key Sections</span>', unsafe_allow_html=True)
        ks = {
            "History": st.session_state.sections.get("History", "") or "—",
            "Findings": st.session_state.sections.get("Findings", "") or "—",
            "Impression/Diagnosis": st.session_state.sections.get("Impression", "") or "—",
            "Recommendations": st.session_state.sections.get("Recommendations", "") or "—"
        }
        st.table(pd.DataFrame(list(ks.items()), columns=["Section", "Content"]).replace("", "—"))
        st.markdown('</div>', unsafe_allow_html=True)

    with colB:
        # Vitals & Labs
        st.markdown('<div class="card"><span class="badge">Vitals</span>', unsafe_allow_html=True)
        vitals = st.session_state.entities.get("Vitals parsed", {})
        vit_df = pd.DataFrame(list(vitals.items()), columns=["Vital", "Value"]) if vitals else pd.DataFrame(columns=["Vital","Value"])
        st.dataframe(vit_df, use_container_width=True)
        st.markdown('</div>', unsafe_allow_html=True)

        st.markdown('<div class="card"><span class="badge">Labs</span>', unsafe_allow_html=True)
        labs = st.session_state.entities.get("Labs parsed", [])
        labs_df = pd.DataFrame(labs) if labs else pd.DataFrame(columns=["Test","Value","Unit"])
        st.dataframe(labs_df, use_container_width=True)
        if not labs_df.empty:
            st.download_button("⬇️ Download labs CSV", labs_df.to_csv(index=False).encode("utf-8"),
                               file_name="labs.csv", mime="text/csv")
        st.markdown('</div>', unsafe_allow_html=True)

    st.markdown('<div class="hr"></div>')

    # Problems table
    st.subheader("Detected problems / highlights")
    if st.session_state.problems:
        problems_df = pd.DataFrame({"Problem / Finding": st.session_state.problems})
        st.dataframe(problems_df, use_container_width=True)
        st.download_button("⬇️ Download problems CSV", problems_df.to_csv(index=False).encode("utf-8"),
                           file_name="problems.csv", mime="text/csv")
    else:
        st.info("No problems auto-detected. Consider manual review.")

    # Conditions table
    st.subheader("Possible conditions (non-diagnostic)")
    if st.session_state.conditions:
        rows = []
        for c in st.session_state.conditions:
            lo, hi = c.get("cost_estimate", (None, None))
            rows.append({
                "Condition": c["name"],
                "Specialist": c["specialist"],
                "Keyword hits": ", ".join(c.get("hits", [])),
                "Typical procedures": ", ".join(c.get("surgeries", [])),
                "Recovery (summary)": " | ".join(c.get("recovery", [])),
                "Red flags": " | ".join(c.get("red_flags", [])),
                "Cost (low USD)": lo,
                "Cost (high USD)": hi,
            })
        cond_df = pd.DataFrame(rows)
        st.dataframe(cond_df, use_container_width=True)
        st.download_button("⬇️ Download conditions CSV",
                           cond_df.to_csv(index=False).encode("utf-8"),
                           file_name="conditions.csv", mime="text/csv")
    else:
        st.info("No condition patterns matched. You can extend the built-in keywords.")

    # Suggested hospitals
    st.subheader("Suggested hospitals (by city)")
    if st.session_state.hospitals:
        hospitals_df = pd.DataFrame({"Hospitals near selected city": st.session_state.hospitals})
        st.dataframe(hospitals_df, use_container_width=True)
        st.download_button("⬇️ Download hospitals CSV",
                           hospitals_df.to_csv(index=False).encode("utf-8"),
                           file_name="hospitals.csv", mime="text/csv")
    else:
        st.write("—")

# 3) Appointment helper
st.header("3) Appointment helper")
patient_name = st.text_input("Your name (for the email draft)")
preferred_time = st.text_input("Preferred appointment time (e.g., 'Next week, morning slot')")

default_specialist = st.session_state.conditions[0]["specialist"] if st.session_state.conditions else "Relevant Specialist"
st.session_state.email_draft = build_email_draft(
    patient_name=patient_name,
    city=city,
    specialist=default_specialist,
    problems=st.session_state.problems,
    conditions=st.session_state.conditions,
    preferred_time=preferred_time
) if st.session_state.extracted_text else ""

if st.session_state.extracted_text:
    st.subheader("Email draft (you can copy/edit)")
    st.code(st.session_state.email_draft or "Run analysis to generate a draft.")

# 4) Download or send
st.header("4) Download or send")

if st.session_state.extracted_text:
    summary = {
        "generated_at": datetime.utcnow().isoformat(),
        "user_location": user_location,
        "city": city,
        "appointment_time": preferred_time,
        "problems": st.session_state.problems,
        "conditions": st.session_state.conditions,
        "hospitals": st.session_state.hospitals,
        "appointment_email": st.session_state.email_draft,
        "entities": st.session_state.entities,
        "sections": st.session_state.sections,
        "disclaimer": "Informational only; not medical advice."
    }

    # JSON download
    json_bytes = json.dumps(summary, indent=2).encode("utf-8")
    st.download_button("⬇️ Download JSON summary", json_bytes,
                       file_name="report_summary.json", mime="application/json")

    # PDF download
    try:
        pdf_bytes = make_pdf(summary)
        st.download_button("⬇️ Download PDF summary", pdf_bytes,
                           file_name="report_summary.pdf", mime="application/pdf")
    except Exception as e:
        st.warning(f"PDF generation issue: {e}. You can download JSON instead.")

    # Optional email send (requires user creds)
    with st.expander("Optional: Send the summary by email (enter your SMTP details)"):
        col1, col2 = st.columns(2)
        with col1:
            sender_email = st.text_input("Your email (SMTP user)")
            sender_pass = st.text_input("App password (recommended) / SMTP password", type="password")
        with col2:
            receiver_email = st.text_input("Recipient email")
            email_subject = st.text_input("Email subject", value="Appointment Request & Clinical Summary")
        email_body = st.text_area("Email body (you can edit)", value=st.session_state.email_draft, height=180)

        if st.button("Send email now"):
            if not (sender_email and sender_pass and receiver_email):
                st.error("Please fill sender email, password, and recipient.")
            else:
                ok, email_status_msg = safe_send_email(
                    sender_email=sender_email,
                    sender_password=sender_pass,
                    receiver_email=receiver_email,
                    subject=email_subject,
                    body=email_body
                )
                st.success(str(email_status_msg)) if ok else st.error(str(email_status_msg))

st.divider()
st.caption("© 2025 — For education/information only. Not a medical device; not medical advice.")
