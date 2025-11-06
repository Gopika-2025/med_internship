# app.py
# -------------------------------------------------------------------
# Clinical Report Helper (Educational) — Streamlit App
# - Upload diagnosis reports (PDF/DOCX/Images)
# - Extract text (pdfplumber, docx2txt, OCR via Tesseract)
# - Lightweight, rule-based clinical info extraction (expandable)
# - Shows possible conditions, surgeries/treatments, recovery tips
# - Rough, location-aware cost ranges (editable)
# - Appointment helper (collects location, preferred time, builds email)
# - Download results (PDF + JSON, plus CSVs for tables)
# - Optional: email the summary via SMTP
#
# IMPORTANT: Not a substitute for professional medical advice/diagnosis.
# -------------------------------------------------------------------

import streamlit as st
import io
import re
import json
from datetime import datetime
from typing import Dict, List, Tuple

import pandas as pd  # NEW

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

# PDF generation (lightweight)
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

# -----------------------------
# Session defaults
# -----------------------------
if "problems" not in st.session_state:
    st.session_state.problems = []
if "conditions" not in st.session_state:
    st.session_state.conditions = []
if "hospitals" not in st.session_state:
    st.session_state.hospitals = []
if "email_draft" not in st.session_state:
    st.session_state.email_draft = ""
if "extracted_text" not in st.session_state:
    st.session_state.extracted_text = ""

# -----------------------------
# Knowledge base (starter)
# You can expand/replace this with a proper clinical KB or model
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
                "Follow wound care instructions; watch for fever, redness, worsening pain"
            ],
            "cost_ranges_usd": {"default": [6000, 18000]},
            "red_flags": [
                "Worsening severe abdominal pain, fever, persistent vomiting",
                "Signs of peritonitis (rigid abdomen) — seek urgent care"
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
                "High fever, jaundice, severe right-upper-quadrant pain",
                "Dark urine / pale stools — seek urgent evaluation"
            ]
        },
        "lumbar_disc_herniation": {
            "keywords": ["lumbar disc herniation", "l4-l5", "l5-s1", "sciatica", "radiculopathy"],
            "specialist": "Orthopedic Spine / Neurosurgeon",
            "surgeries": ["Microdiscectomy (if indicated)"],
            "recovery": [
                "Many improve with conservative care (physiotherapy, analgesia)",
                "If surgery: early mobilization; avoid heavy lifting ~4–6 weeks",
                "Core strengthening per physiotherapist"
            ],
            "cost_ranges_usd": {"default": [8000, 25000]},
            "red_flags": ["Bowel/bladder dysfunction, saddle anesthesia, progressive weakness"]
        },
        "cad": {
            "display": "Coronary Artery Disease",
            "keywords": ["coronary artery disease", "cad", "angina", "nstemi", "stemi", "mi"],
            "specialist": "Cardiologist",
            "surgeries": ["PCI (angioplasty + stent)", "CABG (bypass) — selected cases"],
            "recovery": [
                "Cardiac rehab strongly recommended",
                "Medication adherence (antiplatelets, statin, etc. as prescribed)",
                "Lifestyle: smoking cessation, diet, exercise as cleared by cardiology"
            ],
            "cost_ranges_usd": {"pci": [12000, 30000], "cabg": [35000, 100000], "default": [12000, 100000]},
            "red_flags": [
                "Chest pain at rest, shortness of breath, diaphoresis — emergency",
                "New syncope, arrhythmia, or hemodynamic instability"
            ]
        }
    },
    # Simple region cost modifiers (multiplier)
    "cost_modifiers": {
        "india": 0.25,
        "europe": 1.0,
        "usa": 1.2,
        "uk": 1.1,
        "south_africa": 0.6,
        "brazil": 0.5,
        "uae": 1.0,
        "default": 1.0
    },
    "hospitals": {
        # Example directory — replace with your own
        "chennai": ["Apollo Hospitals Greams Road", "Fortis Malar Hospital", "MIOT International"],
        "coimbatore": ["PSG Hospitals", "KG Hospital", "G Kuppuswamy Naidu Memorial"],
        "bengaluru": ["Manipal Hospitals Old Airport Road", "Aster CMI", "Fortis Bannerghatta"],
        "new york": ["NYU Langone", "Mount Sinai", "NewYork-Presbyterian"],
        "london": ["St Thomas’ Hospital", "Royal London Hospital", "UCLH"],
        "default": ["Local tertiary care hospital", "Accredited specialty center"]
    }
}

# -----------------------------
# Helpers
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

def detect_conditions(text: str) -> List[Dict]:
    """Naive rule-based detection based on KB keywords."""
    found = []
    tnorm = normalize_text(text)
    for key, meta in KB["conditions"].items():
        hits = []
        for kw in meta["keywords"]:
            if kw in tnorm:
                hits.append(kw)
        if hits:
            found.append({
                "key": key,
                "name": meta.get("display", key.replace("_", " ").title())),
                "hits": hits,
                "specialist": meta["specialist"],
                "surgeries": meta["surgeries"],
                "recovery": meta["recovery"],
                "red_flags": meta["red_flags"],
                "cost_ranges_usd": meta["cost_ranges_usd"]
            })
    return found

def summarize_problems(text: str) -> List[str]:
    # Very lightweight extraction of "problem" phrases.
    problems = []
    problem_patterns = [
        r"impression[:\-]\s*(.+)",
        r"diagnosis[:\-]\s*(.+)",
        r"clinical history[:\-]\s*(.+)",
        r"findings[:\-]\s*(.+)"
    ]
    for pat in problem_patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            chunk = m.group(1).strip()
            if chunk and chunk not in problems:
                problems.append(chunk[:300])
    # fallback: pick top lines that look clinical
    if not problems:
        lines = [l.strip() for l in text.splitlines() if len(l.strip()) > 0]
        keywords = ["pain", "lesion", "fracture", "mass", "infection", "infarct", "tear", "hernia", "stone", "blockage", "tumor", "ischemia"]
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
    if any(x in r for x in ["usa", "united states", "new york", "california"]):
        return "usa"
    if any(x in r for x in ["india", "chennai", "coimbatore", "bengaluru", "mumbai", "delhi"]):
        return "india"
    if any(x in r for x in ["uk", "united kingdom", "london", "manchester"]):
        return "uk"
    if any(x in r for x in ["europe", "france", "germany", "spain", "italy", "netherlands"]):
        return "europe"
    if any(x in r for x in ["south africa", "johannesburg", "cape town", "durban"]):
        return "south_africa"
    if any(x in r for x in ["brazil", "são paulo", "rio de janeiro"]):
        return "brazil"
    if any(x in r for x in ["uae", "dubai", "abudhabi", "abu dhabi"]):
        return "uae"
    return "default"

def nearby_hospitals(city: str) -> List[str]:
    if not city:
        return KB["hospitals"]["default"]
    c = city.strip().lower()
    return KB["hospitals"].get(c, KB["hospitals"]["default"])

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
        pdf.ln(3)
        pdf.cell(0, 8, title, ln=True)
        pdf.set_font("Arial", "", 11)

    write_section("Input Overview")
    pdf.multi_cell(0, 6, f"Patient Location: {summary.get('user_location','N/A')}")
    pdf.multi_cell(0, 6, f"Preferred City: {summary.get('city','N/A')}")
    pdf.multi_cell(0, 6, f"Preferred Appointment Time: {summary.get('appointment_time','N/A')}")

    write_section("Detected Problems")
    if summary["problems"]:
        for p in summary["problems"]:
            pdf.multi_cell(0, 6, f"• {p}")
    else:
        pdf.multi_cell(0, 6, "None detected from text. (Manual review advised.)")

    write_section("Possible Conditions")
    if summary["conditions"]:
        for c in summary["conditions"]:
            pdf.multi_cell(0, 6, f"- {c['name']} (Specialist: {c['specialist']})")
            if c.get("surgeries"):
                pdf.multi_cell(0, 6, f"  Surgeries: {', '.join(c['surgeries'])}")
            if c.get("cost_estimate"):
                lo, hi = c["cost_estimate"]
                pdf.multi_cell(0, 6, f"  Cost (rough): ${lo:,} – ${hi:,}")
            if c.get("recovery"):
                for r in c["recovery"]:
                    pdf.multi_cell(0, 6, f"  ▹ {r}")
            if c.get("red_flags"):
                pdf.multi_cell(0, 6, "  Red flags:")
                for rf in c["red_flags"]:
                    pdf.multi_cell(0, 6, f"    - {rf}")
            pdf.ln(1)
    else:
        pdf.multi_cell(0, 6, "No condition patterns matched. (Consider manual review.)")

    write_section("Suggested Hospitals")
    for h in summary["hospitals"]:
        pdf.multi_cell(0, 6, f"• {h}")

    write_section("Appointment Email Draft")
    pdf.multi_cell(0, 6, summary["appointment_email"])

    write_section("Disclaimer")
    pdf.multi_cell(
        0, 6,
        "This summary is informational only and is NOT a medical diagnosis. "
        "Always consult a qualified clinician. If red-flag symptoms are present, seek urgent care."
    )

    return pdf.output(dest="S").encode("latin-1")

def build_email_draft(
    patient_name: str,
    city: str,
    specialist: str,
    problems: List[str],
    conditions: List[Dict],
    preferred_time: str
) -> str:
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

def safe_send_email(
    sender_email: str,
    sender_password: str,
    receiver_email: str,
    subject: str,
    body: str
) -> Tuple[bool, str]:
    try:
        em = EmailMessage()  # renamed from msg to avoid clashes
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

def merge_custom_kb(kb_df: pd.DataFrame):
    """
    Merge a user-provided KB CSV into KB["conditions"].
    Expected columns: name, keywords, specialist, surgeries, recovery, red_flags, cost_low, cost_high
    """
    for _, row in kb_df.iterrows():
        name = str(row.get("name", "")).strip()
        if not name:
            continue
        key = normalize_text(name).replace(" ", "_")
        keywords = [k.strip().lower() for k in re.split(r"[|,]", str(row.get("keywords", ""))) if k.strip()]
        surgeries = [s.strip() for s in re.split(r"[|,]", str(row.get("surgeries", ""))) if s.strip()]
        recovery = [s.strip() for s in re.split(r"[|,]", str(row.get("recovery", ""))) if s.strip()]
        red_flags = [s.strip() for s in re.split(r"[|,]", str(row.get("red_flags", ""))) if s.strip()]
        try:
            cost_low = int(float(row.get("cost_low", 0)))
            cost_high = int(float(row.get("cost_high", 0)))
        except Exception:
            cost_low, cost_high = 0, 0

        KB["conditions"][key] = {
            "display": name,
            "keywords": keywords or [key.replace("_", " ")],
            "specialist": str(row.get("specialist", "Relevant Specialist")),
            "surgeries": surgeries,
            "recovery": recovery,
            "red_flags": red_flags,
            "cost_ranges_usd": {"default": [cost_low, cost_high] if cost_high else [0, 0]},
        }

# -----------------------------
# UI
# -----------------------------
st.title("🩺 Clinical Report Helper (Educational)")
st.caption("Analyze a diagnosis report, get an educational summary, and prep an appointment request. **Not medical advice.**")

with st.expander("Read me first (Safety & Privacy)", expanded=False):
    st.markdown(
        """
- This tool **does not** provide a medical diagnosis. It summarizes text and shows general information and typical options.
- If you have **red-flag** symptoms (severe/worsening pain, chest pain, breathing difficulty, stroke signs, etc.), seek urgent care immediately.
- Do not paste secrets. If you choose to email from the app, credentials are used only in your current session.
        """
    )

# 1) Upload report
st.header("1) Upload your report")
uploaded = st.file_uploader("Upload PDF, DOCX, or a clear image (JPG/PNG)", type=["pdf", "docx", "jpg", "jpeg", "png"])

if uploaded:
    extracted_text, warnings = extract_text_from_file(uploaded)
    st.session_state.extracted_text = extracted_text
    if warnings:
        st.warning("\n".join(warnings))
    if extracted_text:
        with st.expander("Preview extracted text"):
            st.text_area("Text", extracted_text, height=220)
    else:
        st.error("Could not extract text. Try another file or enable OCR (Tesseract).")

# 2) Analysis inputs
st.header("2) Analysis")
user_location = st.text_input("Your region/country (e.g., 'India', 'USA', 'UK', 'South Africa')", help="Used for rough cost ranges.")
city = st.text_input("Preferred city for appointment (e.g., 'Chennai', 'London', 'New York')")

# Optional: custom KB upload
st.markdown("### Optional: Upload a custom conditions KB (CSV)")
st.caption("Columns: name, keywords, specialist, surgeries, recovery, red_flags, cost_low, cost_high")
kb_file = st.file_uploader("Custom KB CSV (optional)", type=["csv"], key="kb_csv")

if kb_file is not None:
    try:
        kb_df = pd.read_csv(kb_file)
        merge_custom_kb(kb_df)
        st.success(f"Loaded {len(kb_df)} custom condition entries.")
    except Exception as e:
        st.error(f"Failed to load custom KB CSV: {e}")

# Run analysis
if st.button("Run analysis", disabled=(not st.session_state.extracted_text)):
    if not st.session_state.extracted_text:
        st.error("Please upload a report first.")
    else:
        problems = summarize_problems(st.session_state.extracted_text)
        detected = detect_conditions(st.session_state.extracted_text)

        region_key = infer_region_key(user_location)
        enriched = []
        for c in detected:
            lo, hi = pick_cost_range(c["cost_ranges_usd"], region_key)
            c["cost_estimate"] = (lo, hi)
            enriched.append(c)

        st.session_state.problems = problems
        st.session_state.conditions = enriched
        st.session_state.hospitals = nearby_hospitals(city)

# Display results as TABLES
if st.session_state.extracted_text:
    st.subheader("Detected problems")
    if st.session_state.problems:
        problems_df = pd.DataFrame({"Problem / Finding": st.session_state.problems})
        st.dataframe(problems_df, use_container_width=True)
        st.download_button(
            "⬇️ Download problems as CSV",
            data=problems_df.to_csv(index=False).encode("utf-8"),
            file_name="problems.csv",
            mime="text/csv",
        )
    else:
        st.info("No problems auto-detected. Consider manual review.")

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
        st.download_button(
            "⬇️ Download conditions as CSV",
            data=cond_df.to_csv(index=False).encode("utf-8"),
            file_name="conditions.csv",
            mime="text/csv",
        )
    else:
        st.info("No condition patterns matched. You can extend the KB keywords or upload a KB CSV.")

    st.subheader("Suggested hospitals (by city)")
    if st.session_state.hospitals:
        hospitals_df = pd.DataFrame({"Hospitals near selected city": st.session_state.hospitals})
        st.dataframe(hospitals_df, use_container_width=True)
        st.download_button(
            "⬇️ Download hospitals as CSV",
            data=hospitals_df.to_csv(index=False).encode("utf-8"),
            file_name="hospitals.csv",
            mime="text/csv",
        )
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
        "disclaimer": "Informational only; not medical advice."
    }

    # JSON download
    json_bytes = json.dumps(summary, indent=2).encode("utf-8")
    st.download_button(
        label="⬇️ Download JSON summary",
        data=json_bytes,
        file_name="report_summary.json",
        mime="application/json",
    )

    # PDF download
    try:
        pdf_bytes = make_pdf(summary)
        st.download_button(
            label="⬇️ Download PDF summary",
            data=pdf_bytes,
            file_name="report_summary.pdf",
            mime="application/pdf",
        )
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
