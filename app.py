# app.py
# -------------------------------------------------------------------
# Clinical Report Helper (Educational) — Streamlit App
# - Upload diagnosis reports (PDF/DOCX/Images)
# - Extract text (pdfplumber, docx2txt, OCR via Tesseract)
# - Lightweight, rule-based clinical info extraction (expandable)
# - Shows possible conditions, surgeries/treatments, recovery tips
# - Rough, location-aware cost ranges (editable)
# - Appointment helper (collects location, preferred time, builds email)
# - Download results (PDF + JSON)
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
            "cost_ranges_usd": {
                "default": [6000, 18000]
            },
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
            "cost_ranges_usd": {
                "default": [7000, 20000]
            },
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
            "cost_ranges_usd": {
                "default": [8000, 25000]
            },
            "red_flags": [
                "Bowel/bladder dysfunction, saddle anesthesia, progressive weakness"
            ]
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
            "cost_ranges_usd": {
                "pci": [12000, 30000],
                "cabg": [35000, 100000],
                "default": [12000, 100000]
            },
            "red_flags": [
                "Chest pain at rest, shortness of breath, diaphoresis — emergency",
                "New syncope, arrhythmia, or hemodynamic instability"
            ]
        }
    },
    # Simple city/country cost modifiers (multiplier)
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
                # OCR images from PDF (fallback)
                warnings.append("Embedded text not found; OCR not implemented for PDFs in this starter.")
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

    # Images
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
    # Very lightweight extraction of "problem" phrases.
    # Expand with clinical NLP later (e.g., negation handling, scispaCy)
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
        guesses = [l for l in lines if any(w in l.lower() for w in ["pain", "lesion", "fracture", "mass", "infection", "infarct", "tear", "hernia", "stone", "blockage", "tumor", "ischemia"])]
        problems = list(dict.fromkeys(guesses[:3]))
    return problems

def pick_cost_range(meta_cost: Dict, location_key: str) -> Tuple[int, int]:
    base_low, base_high = meta_cost.get("default", [0, 0])
    # Adjust by region modifier if available
    mod = KB["cost_modifiers"].get(location_key, KB["cost_modifiers"]["default"])
    return int(base_low * mod), int(base_high * mod)

def infer_region_key(user_region: str) -> str:
    if not user_region:
        return "default"
    r = user_region.strip().lower()
    # very rough mapping
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
        f"Dear Scheduling Team,",
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
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender_email
        msg["To"] = receiver_email
        msg.set_content(body)

        # Basic Gmail SMTP. For other providers, change host/port.
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=context) as server:
            server.login(sender_email, sender_password)
            server.send_message(msg)
        return True, "Email sent."
    except Exception as e:
        return False, f"Email failed: {e}"

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

st.header("1) Upload your report")
uploaded = st.file_uploader("Upload PDF, DOCX, or a clear image (JPG/PNG)", type=["pdf", "docx", "jpg", "jpeg", "png"])

extracted_text = ""
warnings = []

if uploaded:
    extracted_text, warnings = extract_text_from_file(uploaded)
    if warnings:
        st.warning("\n".join(warnings))
    if extracted_text:
        with st.expander("Preview extracted text"):
            st.text_area("Text", extracted_text, height=220)
    else:
        st.error("Could not extract text. Try another file or enable OCR (Tesseract).")

st.header("2) Analysis")
user_location = st.text_input("Your region/country (e.g., 'India', 'USA', 'UK', 'South Africa')", help="Used for rough cost ranges.")
city = st.text_input("Preferred city for appointment (e.g., 'Chennai', 'London', 'New York')")

problems, conditions, hospitals = [], [], []

if st.button("Run analysis", disabled=not extracted_text):
    if not extracted_text:
        st.error("Please upload a report first.")
    else:
        problems = summarize_problems(extracted_text)
        detected = detect_conditions(extracted_text)

        region_key = infer_region_key(user_location)
        enriched = []
        for c in detected:
            lo, hi = pick_cost_range(c["cost_ranges_usd"], region_key)
            c["cost_estimate"] = (lo, hi)
            enriched.append(c)

        conditions = enriched
        hospitals = nearby_hospitals(city)

        # Display results
        st.subheader("Detected problems")
        if problems:
            for p in problems:
                st.markdown(f"- {p}")
        else:
            st.write("• None auto-detected. Consider manual review.")

        st.subheader("Possible conditions (non-diagnostic)")
        if conditions:
            for c in conditions:
                st.markdown(f"**{c['name']}**  \n*Specialist:* {c['specialist']}")
                if c.get("surgeries"):
                    st.markdown(f"• **Typical procedure(s):** {', '.join(c['surgeries'])}")
                if c.get("cost_estimate"):
                    lo, hi = c["cost_estimate"]
                    st.markdown(f"• **Rough cost ({user_location or 'your region'})**: ${lo:,} – ${hi:,}")
                if c.get("recovery"):
                    st.markdown("• **Recovery (typical, varies by patient):**")
                    for r in c["recovery"]:
                        st.markdown(f"  - {r}")
                if c.get("red_flags"):
                    st.markdown("• **Red flags (seek urgent care):**")
                    for rf in c["red_flags"]:
                        st.markdown(f"  - {rf}")
                st.markdown("---")
        else:
            st.info("No condition patterns matched. You can extend the KB keywords to improve coverage.")

        st.subheader("Suggested hospitals (by city)")
        for h in hospitals:
            st.markdown(f"- {h}")

st.header("3) Appointment helper")
patient_name = st.text_input("Your name (for the email draft)")
preferred_time = st.text_input("Preferred appointment time (e.g., 'Next week, morning slot')")

# Decide a default specialist to target in draft
default_specialist = conditions[0]["specialist"] if conditions else "Relevant Specialist"
email_draft = build_email_draft(
    patient_name=patient_name,
    city=city,
    specialist=default_specialist,
    problems=problems,
    conditions=conditions,
    preferred_time=preferred_time
) if extracted_text else ""

if extracted_text:
    st.subheader("Email draft (you can copy/edit)")
    st.code(email_draft or "Run analysis to generate a draft.")

st.header("4) Download or send")

if extracted_text:
    # Build summary structure
    summary = {
        "generated_at": datetime.utcnow().isoformat(),
        "user_location": user_location,
        "city": city,
        "appointment_time": preferred_time,
        "problems": problems,
        "conditions": conditions,
        "hospitals": hospitals,
        "appointment_email": email_draft,
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
        email_body = st.text_area("Email body (you can edit)", value=email_draft, height=180)

        if st.button("Send email now"):
            if not (sender_email and sender_pass and receiver_email):
                st.error("Please fill sender email, password, and recipient.")
            else:
                ok, msg = safe_send_email(
                    sender_email=sender_email,
                    sender_password=sender_pass,
                    receiver_email=receiver_email,
                    subject=email_subject,
                    body=email_body
                )
                st.success(msg) if ok else st.error(msg)

st.divider()
st.caption("© 2025 — For education/information only. Not a medical device; not medical advice.")
