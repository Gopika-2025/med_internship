# =========================================================
# PART 1 — Imports, Config, Cleaners, PDF Builder
# =========================================================

import streamlit as st
import io, os, re, json, uuid
from datetime import datetime, timedelta, date, time
from typing import Dict, Any, List, Tuple

from PIL import Image
import pdfplumber
import docx2txt

from fpdf import FPDF  # using ASCII-safe mode

import yaml

try:
    import pytesseract
    OCR_AVAILABLE = True
except:
    OCR_AVAILABLE = False

st.set_page_config(page_title="Clinical Report Helper", page_icon="🩺", layout="wide")

RULES_FILE = "rules.yaml"
BOOKINGS_FILE = "bookings.json"

# ---------------------------------------------------------
# Clean display text
# ---------------------------------------------------------
def clean_display(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"[\x00-\x1F]", "", str(s)).strip()

# ---------------------------------------------------------
# ASCII fallback cleaner (NO unicode allowed)
# ---------------------------------------------------------
def safe_ascii(s: str) -> str:
    if not s:
        return ""
    replacements = {
        "–": "-",
        "—": "-",
        "…": "...",
        "₹": "Rs ",
        "•": "*",
        "°": " deg",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'"
    }
    for a,b in replacements.items():
        s = s.replace(a,b)
    return s.encode("ascii","replace").decode("ascii")

# ---------------------------------------------------------
# PDF Builder (NO unicode fonts — ONLY ASCII SAFE)
# ---------------------------------------------------------
def build_pdf(lines: List[str]) -> bytes:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_font("Helvetica", size=12)

    for line in lines:
        pdf.multi_cell(0, 7, safe_ascii(line))

    out = pdf.output(dest="S")
    return out if isinstance(out, bytes) else out.encode("latin-1", "replace")
# =========================================================
# PART 2 — YAML Loader, KB Builder, Extract Text
# =========================================================

def load_yaml(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path,"r",encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except:
        return {}

RULES = load_yaml(RULES_FILE)

def build_kb(RULES):
    kb = {
        "conditions": {},
        "hospitals": RULES.get("hospitals", {}),
        "city_cost_modifiers": RULES.get("city_cost_modifiers", {}),
        "general_red_flags": RULES.get("general_rules", {}).get("red_flags", [])
    }

    for d in RULES.get("diseases", []):
        key = re.sub(r"\s+", "_", d["name"].lower())
        kb["conditions"][key] = d

    return kb

KB = build_kb(RULES)

# ---------------------------------------------------------
# Extract Text from PDF / DOCX / Image
# ---------------------------------------------------------
def extract_text(file):
    raw = file.read()
    name = file.name.lower()

    if name.endswith(".pdf"):
        try:
            text = ""
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                for p in pdf.pages:
                    text += p.extract_text() or ""
            return text
        except:
            return ""

    if name.endswith(".docx"):
        try:
            return docx2txt.process(io.BytesIO(raw))
        except:
            return ""

    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        if OCR_AVAILABLE:
            return pytesseract.image_to_string(img)
        return ""
    except:
        return ""
# =========================================================
# PART 3 — Parse Entities + Detect Conditions
# =========================================================

def parse_entities(text):
    ents = {"Name":"","Age":"","Sex":""}
    if not text:
        return ents

    m = re.search(r"(?i)name[:\-]\s*([A-Za-z .'-]+)", text)
    if m: ents["Name"] = clean_display(m.group(1))

    m = re.search(r"(?i)age[:\-]\s*(\d+)", text)
    if m: ents["Age"] = clean_display(m.group(1))

    m = re.search(r"(?i)(sex|gender)[:\-]\s*(male|female|m|f|other)", text)
    if m: ents["Sex"] = clean_display(m.group(2).capitalize())

    return ents

def detect_conditions(text):
    text = text.lower()
    found = []
    for key, d in KB["conditions"].items():
        hits = [k for k in d["keywords"] if k.lower() in text]
        if hits:
            found.append({**d,"hits":hits})

    found.sort(key=lambda x: len(x["hits"]), reverse=True)
    return found

def adjust_cost(cost_range, city):
    if not cost_range or len(cost_range)!=2:
        return (0,0)
    m = KB["city_cost_modifiers"].get(city.lower(),1.0)
    return (int(cost_range[0]*m), int(cost_range[1]*m))
# =========================================================
# PART 4 — Upload Report + Summary + Condition + Treatment
# =========================================================

st.header("📤 Upload Medical Report")

file = st.file_uploader("Upload PDF / DOCX / JPG / PNG", type=["pdf","docx","jpg","jpeg","png"])
if not file:
    st.stop()

text = extract_text(file)
if not text.strip():
    st.error("❌ Could not extract text.")
    st.stop()

st.subheader("📝 Extracted Text")
st.text_area("", text, height=250)

ents = parse_entities(text)
st.subheader("👤 Patient Details")
c1,c2,c3 = st.columns(3)
c1.write("*Name:* "+(ents["Name"] or "—"))
c2.write("*Age:* "+(ents["Age"] or "—"))
c3.write("*Sex:* "+(ents["Sex"] or "—"))

conds = detect_conditions(text)
st.subheader("🔍 Detected Conditions")

if not conds:
    st.warning("No conditions detected.")
    st.stop()

best = conds[0]
st.success(f"*Primary Condition:* {best['name']}")

st.subheader("💊 Treatment Plan")
st.write("*Duration:*", best.get("treatment_span","Not specified"))

st.write("*Procedures:*")
for p in best.get("procedures",[]): st.write("•",safe_ascii(p))

st.write("*Diet:*")
for p in best.get("diet_plan",[]): st.write("•",safe_ascii(p))

st.write("*Lifestyle:*")
for p in best.get("lifestyle_plan",[]): st.write("•",safe_ascii(p))

st.write("*Aftercare:*")
for p in best.get("post_surgery_plan",[]): st.write("•",safe_ascii(p))

low,high = adjust_cost(best["cost_inr"], "bangalore")
st.success(f"Estimated Cost: ₹{low:,} – ₹{high:,}")
# =========================================================
# PART 5 — Hospital Selection + Appointment Form
# =========================================================

st.header("🏥 Book Appointment")

cities = list(KB["hospitals"].keys())
city = st.selectbox("City", cities)

hosp_list = KB["hospitals"].get(city,[])
hospital = st.selectbox("Hospital", [h["name"] for h in hosp_list])

h_obj = next(h for h in hosp_list if h["name"]==hospital)

dept = st.selectbox("Department", ["General"] + list(h_obj.get("departments",{}).keys()))
doctors = h_obj.get("departments",{}).get(dept,["Duty Doctor"])
doctor = st.selectbox("Doctor", doctors)

appt_date = st.date_input("Date", value=date.today())
appt_time = st.time_input("Time", value=time(10,0))

confirm = st.checkbox("Confirm appointment?")
# =========================================================
# PART 6 — PDF + ICS + Save Booking
# =========================================================

if confirm and st.button("Generate Appointment Files"):

    booking_id = str(uuid.uuid4())[:8]

    pdf_lines = [
        "APPOINTMENT CONFIRMATION",
        "",
        f"Booking ID: {booking_id}",
        f"Patient: {ents['Name']}",
        f"Hospital: {hospital}",
        f"City: {city}",
        f"Department: {dept}",
        f"Doctor: {doctor}",
        f"Date: {appt_date}",
        f"Time: {appt_time.strftime('%H:%M')}",
        "",
        "Please bring your medical reports and ID."
    ]

    pdf_bytes = build_pdf(pdf_lines)

    st.download_button(
        "⬇ Download Appointment PDF",
        data=pdf_bytes,
        file_name=f"appointment_{booking_id}.pdf",
        mime="application/pdf"
    )

    start = datetime.combine(appt_date, appt_time)
    end = start + timedelta(minutes=30)

    ics = f"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:{uuid.uuid4()}
DTSTART:{start.strftime("%Y%m%dT%H%M%S")}
DTEND:{end.strftime("%Y%m%dT%H%M%S")}
SUMMARY:Appointment - {hospital}
DESCRIPTION:Doctor: {doctor}
LOCATION:{hospital}, {city}
END:VEVENT
END:VCALENDAR"""

    st.download_button(
        "📅 Add to Calendar (.ics)",
        data=ics.encode("utf-8"),
        file_name=f"appointment_{booking_id}.ics",
        mime="text/calendar"
    )

    # Save booking locally
    try:
        db = []
        if os.path.exists(BOOKINGS_FILE):
            db = json.load(open(BOOKINGS_FILE,"r",encoding="utf-8"))
        db.append({
            "id":booking_id,
            "patient":ents["Name"],
            "condition":best["name"],
            "hospital":hospital,
            "doctor":doctor,
            "city":city,
            "date":str(appt_date),
            "time":appt_time.strftime("%H:%M")
        })
        json.dump(db,open(BOOKINGS_FILE,"w",encoding="utf-8"),indent=2)
    except:
        pass

    st.success(f"Booking saved (ID: {booking_id})")
