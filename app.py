# Part 1/6 — Imports, YAML loader, KB builder, core utilities
# -------------------------------------------------------------------
# Clinical Report Helper (YAML-driven, full feature set)
# This file is split into parts. Paste Part 1 first, then Parts 2..6 in order.
# -------------------------------------------------------------------

import streamlit as st
import io
import re
import json
import uuid
import os
from typing import Dict, List, Tuple, Any
from datetime import datetime, date, time, timedelta

import pandas as pd
import pdfplumber
import docx2txt
from PIL import Image

# Optional OCR import
try:
    import pytesseract
    OCR_AVAILABLE = True
except Exception:
    pytesseract = None
    OCR_AVAILABLE = False

from fpdf import FPDF
import smtplib
import ssl
from email.message import EmailMessage

# YAML loader (optional)
try:
    import yaml
    YAML_AVAILABLE = True
except Exception:
    yaml = None
    YAML_AVAILABLE = False

# Files / constants
RULES_FILE = "rules.yaml"
BOOKINGS_FILE = "bookings.json"

# -----------------------------
# Streamlit page config + styling
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
    "receipt_pdf_bytes": b"",
}.items():
    if k not in st.session_state:
        st.session_state[k] = v

# -----------------------------
# YAML loader + KB builder
# -----------------------------
def load_rules_yaml(path: str) -> Dict[str, Any]:
    """
    Load rules.yaml (if PyYAML available). Return an empty dict on failure.
    The file must have top-level keys: general_rules, city_cost_modifiers (optional),
    hospitals, diseases.
    """
    if not YAML_AVAILABLE:
        return {}
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data or {}
    except Exception:
        return {}

def normalize_city_key(k: str) -> str:
    return (k or "").strip().lower()

def build_internal_kb(rules: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert rules.yaml structure into an internal KB used by the app.
    Output schema:
    {
      "city_cost_modifiers": {city_key: float, ... , "default": 1.0},
      "hospitals": { city_key: [ {name, email, departments: {dept: [doctors]}}, ... ], ... },
      "conditions": { cond_key: {display, keywords, severity_rules, procedures, recovery_recos, cost_inr, about}, ... },
      "general_red_flags": [ ... ]
    }
    """
    kb: Dict[str, Any] = {
        "city_cost_modifiers": {},
        "hospitals": {},
        "conditions": {},
        "general_red_flags": []
    }

    # city_cost_modifiers: may be top-level or inside a `cities` section
    # Accept either:
    # city_cost_modifiers:
    #   mumbai: 1.25
    # or
    # cities:
    #   mumbai:
    #     cost_modifier: 1.25
    #     hospitals: [...]
    city_costs = rules.get("city_cost_modifiers", {}) or {}
    for k, v in (city_costs.items() if isinstance(city_costs, dict) else []):
        try:
            kb["city_cost_modifiers"][normalize_city_key(k)] = float(v)
        except Exception:
            pass

    # Also inspect 'cities' section (optional)
    cities_section = rules.get("cities", {}) or {}
    if isinstance(cities_section, dict):
        for c, info in cities_section.items():
            ck = normalize_city_key(c)
            if isinstance(info, dict):
                cm = info.get("cost_modifier")
                if cm is not None:
                    try:
                        kb["city_cost_modifiers"][ck] = float(cm)
                    except Exception:
                        pass

    # ensure default modifier
    kb["city_cost_modifiers"].setdefault("default", 1.0)

    # hospitals — top-level 'hospitals' expected: mapping city -> list of hospital objects
    hospitals_root = rules.get("hospitals", {}) or {}
    if isinstance(hospitals_root, dict):
        for city_name, hlist in hospitals_root.items():
            ck = normalize_city_key(city_name)
            kb["hospitals"].setdefault(ck, [])
            if isinstance(hlist, list):
                for h in hlist:
                    if not isinstance(h, dict):
                        continue
                    name = h.get("name", "") or ""
                    email = h.get("email", "") or ""
                    departments = h.get("departments", {}) or {}
                    dept_clean: Dict[str, List[str]] = {}
                    if isinstance(departments, dict):
                        for dname, docs in departments.items():
                            if isinstance(docs, list):
                                dept_clean[dname] = docs
                            elif docs is None:
                                dept_clean[dname] = []
                            else:
                                dept_clean[dname] = [docs]
                    kb["hospitals"][ck].append({"name": name, "email": email, "departments": dept_clean})

    # also accept hospitals under cities section
    if isinstance(cities_section, dict):
        for city_name, cinfo in cities_section.items():
            ck = normalize_city_key(city_name)
            if isinstance(cinfo, dict):
                hlist = cinfo.get("hospitals", []) or []
                kb["hospitals"].setdefault(ck, [])
                for h in (hlist if isinstance(hlist, list) else []):
                    if not isinstance(h, dict):
                        continue
                    name = h.get("name", "") or ""
                    email = h.get("email", "") or ""
                    departments = h.get("departments", {}) or {}
                    dept_clean: Dict[str, List[str]] = {}
                    if isinstance(departments, dict):
                        for dname, docs in departments.items():
                            if isinstance(docs, list):
                                dept_clean[dname] = docs
                            elif docs is None:
                                dept_clean[dname] = []
                            else:
                                dept_clean[dname] = [docs]
                    kb["hospitals"][ck].append({"name": name, "email": email, "departments": dept_clean})
                # also capture cost_modifier if present
                cm = cinfo.get("cost_modifier")
                if cm is not None:
                    try:
                        kb["city_cost_modifiers"][ck] = float(cm)
                    except Exception:
                        pass

    # ensure a default hospital entry exists
    if "default" not in kb["hospitals"]:
        kb["hospitals"]["default"] = [{"name": "Accredited tertiary center near you", "email": "", "departments": {"General": ["Duty Doctor"]}}]

    # diseases -> conditions
    diseases = rules.get("diseases", []) or []
    if isinstance(diseases, list):
        for d in diseases:
            if not isinstance(d, dict):
                continue
            name = (d.get("name") or "unknown").strip()
            key = re.sub(r"\s+", "_", name.lower())
            kb["conditions"][key] = {
                "display": name,
                "keywords": d.get("keywords", []) or [],
                "severity_rules": d.get("severity_rules", {}) or {},
                "procedures": d.get("procedures", []) or [],
                "recovery_recos": d.get("recovery_recos", []) or [],
                "cost_inr": d.get("cost_inr", [0, 0]) or [0, 0],
                "about": d.get("about", "") or ""
            }

    # general red flags
    general_rules = rules.get("general_rules", {}) or {}
    kb["general_red_flags"] = general_rules.get("red_flags", []) or []

    # final defaults
    kb["city_cost_modifiers"].setdefault("default", 1.0)
    return kb

# Load YAML and build KB once at startup
RULES = load_rules_yaml(RULES_FILE)
KB = build_internal_kb(RULES)

# -----------------------------
# Core utility functions used by the rest of the app
# -----------------------------
def normalize_text(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip().lower()

def list_india_cities() -> List[str]:
    ks = [k for k in KB.get("hospitals", {}).keys() if k != "default"]
    return sorted(ks)

def india_adjust_cost(base: List[int], city: str) -> Tuple[int, int]:
    if not base or len(base) != 2:
        return (0, 0)
    m = KB.get("city_cost_modifiers", {}).get(normalize_city_key(city), KB.get("city_cost_modifiers", {}).get("default", 1.0))
    try:
        return (int(base[0] * float(m)), int(base[1] * float(m)))
    except Exception:
        return (int(base[0]), int(base[1]))

def nearby_hospitals(city: str) -> List[Dict[str, Any]]:
    return KB.get("hospitals", {}).get(normalize_city_key(city), KB.get("hospitals", {}).get("default", []))

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

# End of Part 1/6
# Paste Part 2/6 next (Extraction + Parsing + Detection)
# Part 2/6 — Extraction + Parsing + Condition Detection
# -------------------------------------------------------------------

# -----------------------------
# Extract text from uploaded files
# -----------------------------
def extract_text_from_file(uploaded) -> Tuple[str, List[str]]:
    warnings = []
    name = uploaded.name.lower()
    data = uploaded.read()

    # PDF extraction
    if name.endswith(".pdf"):
        try:
            text = ""
            with pdfplumber.open(io.BytesIO(data)) as pdf:
                for page in pdf.pages:
                    t = page.extract_text() or ""
                    text += "\n" + t
            return text.strip(), warnings
        except Exception as e:
            return "", [f"PDF read error: {str(e)}"]

    # DOCX extraction
    if name.endswith(".docx"):
        try:
            buf = io.BytesIO(data)
            return (docx2txt.process(buf) or ""), warnings
        except Exception as e:
            return "", [f"DOCX read error: {str(e)}"]

    # Image (JPG/PNG) extraction with OCR
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
        if OCR_AVAILABLE:
            text = pytesseract.image_to_string(im) or ""
            return text, warnings
        else:
            warnings.append("OCR not available — install Tesseract for image text extraction.")
            return "", warnings
    except Exception:
        return "", ["Unsupported file. Please upload PDF, DOCX, JPG, or PNG."]

# -----------------------------
# Parse patient entities
# -----------------------------
def parse_entities(text: str) -> Dict[str, Any]:
    ents = {}

    # Name
    m = re.search(r"(?i)\b(name|patient\s*name)\s*[:\-]\s*([A-Za-z ,.'-]{2,60})", text or "")
    ents["Name"] = m.group(2).strip() if m else ""

    # Age
    m = re.search(r"(?i)\b(age)\s*[:\-]\s*(\d{1,3})", text or "")
    ents["Age"] = m.group(2) if m else ""

    # Sex / Gender
    m = re.search(r"(?i)\b(sex|gender)\s*[:\-]\s*(male|female|m|f|other)", text or "")
    ents["Sex"] = m.group(2).capitalize() if m else ""

    return ents

# -----------------------------
# Summarize problems / impressions from text
# -----------------------------
def summarize_problems(text: str) -> List[str]:
    probs = []

    patterns = [
        r"(?i)impression\s*[:\-]\s*(.+)",
        r"(?i)diagnosis\s*[:\-]\s*(.+)",
        r"(?i)conclusion\s*[:\-]\s*(.+)",
        r"(?i)findings\s*[:\-]\s*(.+)",
    ]

    for pat in patterns:
        for m in re.finditer(pat, text or ""):
            s = m.group(1).strip()
            if s and s not in probs:
                probs.append(s[:300])

    if probs:
        return probs

    # fallback: line heuristic
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    keywords = [
        "pain", "lesion", "fracture", "mass", "infection", "infarct", "tear",
        "hernia", "stone", "blockage", "tumor", "ischemia", "angina", "colic",
    ]
    guesses = [l for l in lines if any(w in l.lower() for w in keywords)]
    return list(dict.fromkeys(guesses[:3]))  # top 3

# -----------------------------
# Condition / disease detection
# -----------------------------
def matches_keyword(text: str, keyword: str) -> bool:
    kw = keyword.lower().strip()
    if len(kw) < 3:
        return False
    return bool(re.search(rf"\b{re.escape(kw)}\b", text.lower()))

def detect_conditions(text: str) -> List[Dict]:
    t = normalize_text(text)
    results = []

    for key, meta in KB.get("conditions", {}).items():
        hits = [kw for kw in meta.get("keywords", []) if matches_keyword(t, kw)]
        if hits:
            results.append({
                "key": key,
                "display": meta.get("display", key),
                "hits": hits,
                "keywords": meta.get("keywords", []),
                "severity_rules": meta.get("severity_rules", {}),
                "procedures": meta.get("procedures", []),
                "recovery_recos": meta.get("recovery_recos", []),
                "cost_inr": meta.get("cost_inr", [0, 0]),
                "about": meta.get("about", ""),
            })

    # Sort by number of keyword matches
    results.sort(key=lambda x: len(x["hits"]), reverse=True)
    return results

# -----------------------------
# Severity scoring
# -----------------------------
def severity_score(text: str, cond: Dict) -> int:
    t = normalize_text(text)
    score = 0

    # General red flags from YAML
    for rf in KB.get("general_red_flags", []):
        if rf.lower() in t:
            score += 10

    # Condition-specific red flags
    cond_flags = cond.get("severity_rules", {}).get("red_flags", []) or []
    for rf in cond_flags:
        if rf.lower() in t:
            score += 12

    # Keyword density boost
    score += min(5, len(cond.get("hits", []))) * 4

    # Cap severity
    score = min(score, 95)

    # If text mentions "normal"
    normal_markers = [
        "normal study", "within normal limits", "no acute",
        "normal xray", "normal chest xray", "normal chest",
    ]
    if any(nm in t for nm in normal_markers):
        score = min(score, 5)

    return max(0, score)
# Part 3/6 — Main UI: Upload, Parsing, Summary, Conditions display
# -------------------------------------------------------------------

# Sidebar — India city selection (from KB)
st.sidebar.header("India Location")
cities = [""] + list_india_cities()
default_index = cities.index("chennai") if "chennai" in cities else 0
city = st.sidebar.selectbox("Choose your city (India)", cities, index=default_index)
st.sidebar.caption("Used for cost estimates & hospital suggestions in India.")

# Show YAML/PyYAML warnings up-front
if not YAML_AVAILABLE:
    st.error("PyYAML is not installed. Add PyYAML to requirements.txt and redeploy (e.g., PyYAML==6.0.1).")
elif not RULES:
    st.warning(f"rules.yaml not found or empty at '{RULES_FILE}'. Disease detection and hospitals will be limited.")

# Page header
st.title("🩺 Clinical Report Helper (India)")
st.write('<span class="small-muted">For education/information only. Not medical advice.</span>', unsafe_allow_html=True)
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)

# Two-column layout for upload + status
colL, colR = st.columns([2, 1], vertical_alignment="top")
with colL:
    st.markdown('<div class="section-title">1) Upload your report</div>', unsafe_allow_html=True)
    uploaded = st.file_uploader("Upload PDF, DOCX, or a clear image (JPG/PNG)", type=["pdf", "docx", "jpg", "jpeg", "png"])
with colR:
    st.markdown('<div class="section-title">Status</div>', unsafe_allow_html=True)
    if st.session_state.extracted_text:
        st.success("Report text extracted")
    else:
        st.info("Waiting for upload…")

# Auto-extract when file uploaded
if uploaded is not None:
    text, warns = extract_text_from_file(uploaded)
    st.session_state.extracted_text = text
    if warns:
        st.warning("\n".join(warns))

# If we have extracted text, parse and show results
if st.session_state.extracted_text:
    # Parse entities and problems
    ents = parse_entities(st.session_state.extracted_text)
    probs = summarize_problems(st.session_state.extracted_text)

    # Guard: detect if report explicitly normal
    tn = normalize_text(st.session_state.extracted_text)
    normal_markers = [
        "normal chest x-ray", "normal chest xray", "normal study",
        "within normal limits", "no acute", "impression: normal"
    ]
    is_normal = any(nm in tn for nm in normal_markers)

    detected = [] if is_normal else detect_conditions(st.session_state.extracted_text)

    # Build alternatives with severity% and cost by city
    alts = []
    best = None
    if detected:
        for d in detected:
            pct = severity_score(st.session_state.extracted_text, d)
            lo, hi = india_adjust_cost(d.get("cost_inr", [0, 0]), city)
            alts.append({
                "key": d.get("key", d.get("display", "")).lower(),
                "name": d.get("display", d.get("name", "")),
                "hits": d.get("hits", []),
                "about": d.get("about", ""),
                "procedures": d.get("procedures", []) or d.get("actions", []),
                "recovery_recos": d.get("recovery_recos", []) or d.get("recovery", []),
                "severity_rules": d.get("severity_rules", {}),
                "cost_inr": d.get("cost_inr", [0, 0]),
                "severity_pct": pct,
                "cost_low": lo,
                "cost_high": hi
            })
        alts.sort(key=lambda x: (len(x.get("hits", [])), x.get("severity_pct", 0)), reverse=True)
        best = alts[0]
        st.session_state.alt_conditions = alts[1:]
    else:
        st.session_state.alt_conditions = []

    st.session_state.entities = ents
    st.session_state.problems = probs
    st.session_state.best_condition = best
    st.session_state.hospitals = [h.get("name", "") for h in nearby_hospitals(city)]

    # ---------- 2) Patient summary ----------
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">2) Patient summary</div>', unsafe_allow_html=True)
    c1, c2 = st.columns([1.2, 2], vertical_alignment="top")
    with c1:
        df_patient = pd.DataFrame([
            ["Name", ents.get("Name", "") or "—"],
            ["Age", ents.get("Age", "") or "—"],
            ["Sex", ents.get("Sex", "") or "—"],
            ["City (India)", city or "—"],
        ], columns=["Field", "Value"])
        st.table(df_patient)
    with c2:
        df_probs = pd.DataFrame({"Problem / Impression": st.session_state.problems or ["—"]})
        st.dataframe(df_probs, use_container_width=True)

    # ---------- 3) Condition & care plan ----------
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
            ["Likely condition", best.get("name", "—")],
            ["About", best.get("about", "")],
            ["What to do now", " ; ".join(best.get("procedures", []))],
            ["Recovery (typical)", " | ".join(best.get("recovery_recos", []))],
            ["Estimated cost (INR)", f"₹{best.get('cost_low',0):,} – ₹{best.get('cost_high',0):,}"],
        ], columns=["Item", "Details"])
        st.table(df_cond)

        if best.get("severity_rules", {}).get("red_flags"):
            st.info("Red flags: " + " | ".join(best.get("severity_rules", {}).get("red_flags")))
    else:
        if is_normal:
            st.success("Report appears normal / no acute findings detected.")
        else:
            st.warning("No specific condition pattern matched. If your report says 'Normal', this can be expected. Otherwise, consult a clinician for personalized advice.")

    # Show alternative matches (if any)
    if st.session_state.alt_conditions:
        with st.expander(f"Alternative matches ({len(st.session_state.alt_conditions)})"):
            for alt in st.session_state.alt_conditions:
                st.write(f"**{alt.get('name')}** — Severity: {alt.get('severity_pct')}% — Hits: {', '.join(alt.get('hits', []))}")
                st.write(alt.get("about", ""))
                st.markdown("---")

    # Continue to Part 4 (Hospital booking UI)
# End of Part 3/6
# Part 4/6 — Hospital Suggestions + Appointment Helper + Email Draft
# -------------------------------------------------------------------

    # ---------- 4) Suggested hospitals ----------
    st.markdown('<div class="section-title">4) Suggested hospitals (India)</div>', unsafe_allow_html=True)

    hospitals_list = st.session_state.hospitals or ["—"]
    df_hospitals = pd.DataFrame({"Hospitals": hospitals_list})
    st.dataframe(df_hospitals, use_container_width=True)

    # ---------- 5) Appointment Helper ----------
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">5) Appointment helper</div>', unsafe_allow_html=True)

    colA, colB = st.columns(2)

    with colA:
        patient_name = st.text_input("Your name (for email)", value=ents.get("Name", ""))
        appt_date = st.date_input("Preferred date", date.today())
        appt_time = st.time_input("Preferred time", time(10, 0))

    with colB:
        hospital_sel = st.selectbox("Choose a hospital", hospitals_list)
        patient_phone = st.text_input("Phone (optional)")
        patient_email = st.text_input("Email (optional)")

    # Pick specialist name
    if best:
        specialist = best.get("name", "Specialist")
    else:
        specialist = "Relevant Specialist"

    # Generate booking email text
    email_lines = [
        f"Subject: Appointment Request — {specialist}",
        "",
        "Dear Scheduling Team,",
        "",
        f"My name is {patient_name or 'Patient'}. I would like to book an appointment at {hospital_sel or 'your hospital'}.",
        "",
        "Summary:",
    ]

    # Report highlights
    if probs:
        email_lines.append("• Report highlights: " + "; ".join(probs[:3]))
    if best:
        email_lines.append(f"• Possible condition (non-diagnostic): {best.get('name')}")

    email_lines.append(f"• Preferred slot: {appt_date} at {appt_time}")

    # Contact
    if patient_phone:
        email_lines.append(f"• Phone: {patient_phone}")
    if patient_email:
        email_lines.append(f"• Email: {patient_email}")

    email_lines += [
        "",
        "I can share my reports if required.",
        "",
        "Thank you,",
        f"{patient_name or 'Patient'}"
    ]

    booking_email = "\n".join(email_lines)

    # Display email
    st.code(booking_email)

    # Prepare appointment datetime
    from datetime import datetime as dt
    appt_dt = dt.combine(appt_date, appt_time)

    # For ICS creation later
    st.session_state.latest_appointment_dt = appt_dt
    st.session_state.latest_hospital = hospital_sel
    st.session_state.latest_patient_name = patient_name
    st.session_state.latest_patient_email = patient_email
    st.session_state.latest_patient_phone = patient_phone
    st.session_state.latest_email_draft = booking_email
# Part 5/6 — PDF Builder + ICS Calendar File + Downloads
# -------------------------------------------------------------------

# ------------ PDF Builder ------------
def build_full_pdf(entities, problems, best, city, hospitals, appointment_dt):
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=12)
    pdf.add_page()

    pdf.set_font("Arial", "B", 14)
    pdf.cell(0, 10, "Clinical Report Helper — Full Report (India)", ln=True)

    pdf.set_font("Arial", "", 11)
    pdf.cell(0, 7, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True)

    # Sections
    def section(title):
        pdf.ln(4)
        pdf.set_font("Arial", "B", 12)
        pdf.cell(0, 8, title, ln=True)
        pdf.set_font("Arial", "", 11)

    # Patient section
    section("Patient Details")
    pdf.multi_cell(0, 6, f"Name: {entities.get('Name','—')}")
    pdf.multi_cell(0, 6, f"Age: {entities.get('Age','—')}")
    pdf.multi_cell(0, 6, f"Sex: {entities.get('Sex','—')}")
    pdf.multi_cell(0, 6, f"City: {city or '—'}")

    # Problems
    section("Issues / Impressions")
    if problems:
        for p in problems:
            pdf.multi_cell(0, 6, f"* {p}")
    else:
        pdf.multi_cell(0, 6, "—")

    # Condition section
    section("Condition & Plan")
    if best:
        pdf.multi_cell(0, 6, f"Likely Condition: {best['name']}")
        pdf.multi_cell(0, 6, f"About: {best.get('about','')}")
        pdf.multi_cell(0, 6, f"Specialist: {best.get('specialist','')}")
        pdf.multi_cell(0, 6, f"Immediate Steps: {', '.join(best.get('actions', []))}")
        pdf.multi_cell(0, 6, f"Recovery: {', '.join(best.get('recovery', []))}")
        pdf.multi_cell(0, 6, f"Severity Score: {best.get('severity_pct',0)}%")
        pdf.multi_cell(0, 6, f"Estimated Cost (INR): Rs {best.get('cost_low',0):,} – {best.get('cost_high',0):,}")
    else:
        pdf.multi_cell(0, 6, "No specific condition detected.")

    # Hospitals
    section("Suggested Hospitals")
    if hospitals:
        for h in hospitals:
            pdf.multi_cell(0, 6, f"- {h}")
    else:
        pdf.multi_cell(0, 6, "—")

    # Appointment
    section("Appointment (Requested)")
    if appointment_dt:
        pdf.multi_cell(0, 6, f"Preferred Slot: {appointment_dt}")
    else:
        pdf.multi_cell(0, 6, "—")

    # Disclaimer
    section("Disclaimer")
    pdf.multi_cell(0, 6, "Informational only — not a medical diagnosis. Consult a clinician.")

    return pdf.output(dest="S").encode("latin-1", "replace")


# ------------ ICS Calendar Builder ------------
def build_ics(patient, hospital, city, dt_value):
    if not dt_value:
        return b""

    dt_start = dt_value.strftime("%Y%m%dT%H%M%S")
    dt_end = (dt_value + timedelta(minutes=30)).strftime("%Y%m%dT%H%M%S")

    ics_text = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//ClinicalHelper//EN
BEGIN:VEVENT
UID:{uuid.uuid4()}
DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}
DTSTART:{dt_start}
DTEND:{dt_end}
SUMMARY:Appointment for {patient} at {hospital}
DESCRIPTION:Generated by Clinical Report Helper
LOCATION:{hospital}, {city}
END:VEVENT
END:VCALENDAR
"""
    return ics_text.encode("utf-8")


# ------------ Generate Full Report (PDF + ICS) ------------
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
st.markdown('<div class="section-title">6) Download Full Report (PDF)</div>', unsafe_allow_html=True)

# Build full PDF
full_pdf_bytes = build_full_pdf(
    entities=st.session_state.entities,
    problems=st.session_state.problems,    # <-- FIXED
    best=st.session_state.best_condition,
    city=city,
    hospitals=st.session_state.hospitals,
    appointment_dt=st.session_state.latest_appointment_dt
)

st.session_state.latest_pdf_bytes = full_pdf_bytes

# Download PDF button
st.download_button(
    "⬇️ Download Full Report (PDF)",
    data=full_pdf_bytes,
    file_name="clinical_full_report.pdf",
    mime="application/pdf"
)

# Build ICS
ics_bytes = build_ics(
    patient=st.session_state.entities.get("Name", "Patient"),
    hospital=st.session_state.latest_hospital,
    city=city,
    dt_value=st.session_state.latest_appointment_dt
)

st.session_state.latest_ics_bytes = ics_bytes

if ics_bytes:
    st.download_button(
        "📅 Download Appointment (.ics)",
        data=ics_bytes,
        file_name="appointment.ics",
        mime="text/calendar"
    )

# Part 6/6 — Booking persistence, Email sending, My Bookings, Footer
# -------------------------------------------------------------------

    # -----------------------------
    # Booking persistence (JSON)
    # -----------------------------
    def load_bookings() -> List[Dict[str, str]]:
        if not os.path.exists(BOOKINGS_FILE):
            return []
        try:
            with open(BOOKINGS_FILE, "r", encoding="utf-8") as f:
                rows = json.load(f)
                return rows or []
        except Exception:
            return []

    def save_bookings(rows: List[Dict[str, str]]):
        try:
            with open(BOOKINGS_FILE, "w", encoding="utf-8") as f:
                json.dump(rows, f, indent=2, ensure_ascii=False)
        except Exception as e:
            st.warning(f"Failed to save booking: {e}")

    # -----------------------------
    # Receipt PDF builder (simple)
    # -----------------------------
    def build_receipt_pdf(booking: Dict[str, str]) -> bytes:
        pdf = FPDF()
        pdf.set_auto_page_break(auto=True, margin=12)
        pdf.add_page()
        pdf.set_font("Arial", "B", 14)
        pdf.cell(0, 10, "Appointment Booking Receipt", ln=True)
        pdf.set_font("Arial", "", 11)
        pdf.cell(0, 7, f"Booking ID: {booking.get('booking_id','')}", ln=True)
        pdf.cell(0, 7, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}", ln=True)
        pdf.ln(4)
        for k in ["patient_name", "patient_phone", "patient_email", "city", "hospital", "department", "date", "time"]:
            pdf.set_font("Arial", "B", 11)
            pdf.cell(50, 7, f"{k.replace('_',' ').title()}:")
            pdf.set_font("Arial", "", 11)
            pdf.multi_cell(0, 7, str(booking.get(k, "—")))
        pdf.ln(4)
        pdf.set_font("Arial", "", 10)
        pdf.multi_cell(0, 6, "Please arrive 15 minutes early with your ID and past reports. This is a confirmation of your requested slot.")
        return pdf.output(dest="S").encode("latin-1", "replace")

    # -----------------------------
    # Confirm booking (save + receipt)
    # -----------------------------
    if st.button("✅ Confirm Booking"):
        # gather fields (fall back to session values)
        b_patient_name = patient_name or st.session_state.entities.get("Name", "") or st.session_state.latest_patient_name or "Patient"
        b_phone = patient_phone or st.session_state.latest_patient_phone or ""
        b_email = patient_email or st.session_state.latest_patient_email or ""
        b_city = city or ""
        b_hospital = hospital_sel or st.session_state.latest_hospital or ""
        b_department = best.get("name") if best else "General"
        b_date = str(appt_date)
        b_time = str(appt_time)

        # create booking record
        booking_id = str(uuid.uuid4())[:8]
        booking = {
            "booking_id": booking_id,
            "patient_name": b_patient_name,
            "patient_phone": b_phone,
            "patient_email": b_email,
            "city": b_city,
            "hospital": b_hospital,
            "department": b_department,
            "date": b_date,
            "time": b_time
        }

        rows = load_bookings()
        rows.append(booking)
        save_bookings(rows)

        # Build receipt + ICS
        receipt_bytes = build_receipt_pdf(booking)
        st.session_state.receipt_pdf_bytes = receipt_bytes

        appt_dt_local = st.session_state.latest_appointment_dt or datetime.combine(appt_date, appt_time)
        ics_bytes_book = build_ics(b_patient_name, b_hospital, b_city, appt_dt_local)
        st.session_state.latest_ics_bytes = ics_bytes_book

        st.success(f"Booking confirmed — ID: {booking_id}")
        st.download_button("⬇️ Download Booking Receipt (PDF)", data=receipt_bytes,
                           file_name=f"booking_{booking_id}.pdf", mime="application/pdf")
        st.download_button("📅 Add booking to Calendar (.ics)", data=ics_bytes_book,
                           file_name=f"booking_{booking_id}.ics", mime="text/calendar")
        st.info("Use the email section below to email the hospital with attachments.")

    # -----------------------------
    # Flexible email sender function
    # -----------------------------
    def send_email_flexible(sender_email: str, sender_password: str,
                            to_email: str, subject: str, body: str,
                            attachments: List[Tuple[bytes, str, str]],
                            bcc_self: bool, host: str, port: int, security_mode: str, timeout: int) -> Tuple[bool, str]:
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = sender_email
        msg["To"] = to_email
        if bcc_self:
            msg["Bcc"] = sender_email
        msg.set_content(body)

        for data, fname, mime in attachments:
            if not data:
                continue
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
            # fallback attempt for STARTTLS on 587 -> SSL 465
            if security_mode.startswith("STARTTLS") and port == 587:
                try:
                    with smtplib.SMTP_SSL(host=host, port=465, context=ssl.create_default_context(), timeout=timeout) as server:
                        server.login(sender_email, sender_password)
                        server.send_message(msg)
                    return True, "Email sent (fallback SSL:465)."
                except Exception as e2:
                    return False, f"Email failed on STARTTLS:587 and SSL:465. Primary: {e}; Fallback: {e2}"
            return False, f"Email failed: {e}"

    # -----------------------------
    # Email section UI
    # -----------------------------
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
    st.markdown('<div class="section-title">Email booking / confirmation</div>', unsafe_allow_html=True)

    colE1, colE2 = st.columns(2)
    with colE1:
        sender_email_input = st.text_input("Your email (SMTP user) e.g. yourname@gmail.com")
        sender_pass_input = st.text_input("App password / SMTP password", type="password")
        send_copy_to_me = st.checkbox("Send me a copy (BCC)", value=True)
    with colE2:
        # default hospital email: try to pick from YAML hospital entry
        fallback = ""
        try:
            # find hospital object
            for h in nearby_hospitals(city):
                if h.get("name") == hospital_sel:
                    fallback = h.get("email", "") or fallback
        except Exception:
            fallback = ""
        hospital_recipient = st.text_input("Hospital / Recipient email", value=fallback)
        email_subject = st.text_input("Email subject", value=f"Appointment request — {best.get('name') if best else 'Booking'}")

    with st.expander("Advanced SMTP settings"):
        smtp_host = st.text_input("SMTP host", value="smtp.gmail.com")
        smtp_port = st.number_input("SMTP port", min_value=1, max_value=65535, value=587, step=1)
        security = st.selectbox("Security", ["STARTTLS (recommended, port 587)", "SSL/TLS (port 465)"])
        smtp_timeout = st.number_input("Timeout (seconds)", min_value=5, max_value=120, value=25)

    # Compose email body (default)
    email_body_lines = ["Dear Scheduling Team,", ""]
    if st.session_state.problems:
        email_body_lines.append("Report highlights: " + "; ".join(st.session_state.problems[:3]))
    if st.session_state.best_condition:
        email_body_lines.append(f"Possible condition (non-diagnostic): {st.session_state.best_condition.get('name')} | Severity: {st.session_state.best_condition.get('severity_pct')}%")
    email_body_lines.append("")
    email_body_lines.append("Please find attached my clinical summary and booking receipt (if applicable).")
    email_body_lines += ["", "Thank you,", st.session_state.entities.get("Name", "Patient") or "Patient"]
    email_body = "\n".join(email_body_lines)
    st.code(email_body)

    # attachments: full report + optional receipt + optional ics
    attachments = []
    if st.session_state.latest_pdf_bytes:
        attachments.append((st.session_state.latest_pdf_bytes, "clinical_report.pdf", "application/pdf"))
    if st.session_state.receipt_pdf_bytes:
        attachments.append((st.session_state.receipt_pdf_bytes, "booking_receipt.pdf", "application/pdf"))
    if st.session_state.latest_ics_bytes:
        attachments.append((st.session_state.latest_ics_bytes, "appointment.ics", "text/calendar"))

    if st.button("📧 Send Email with Attachments"):
        if not (sender_email_input and sender_pass_input and hospital_recipient):
            st.error("Please fill: Your email, your SMTP password, and recipient email.")
        else:
            ok, msg = send_email_flexible(
                sender_email=sender_email_input,
                sender_password=sender_pass_input,
                to_email=hospital_recipient,
                subject=email_subject,
                body=email_body,
                attachments=attachments,
                bcc_self=send_copy_to_me,
                host=smtp_host,
                port=int(smtp_port),
                security_mode=security,
                timeout=int(smtp_timeout),
            )
            if ok:
                st.success(msg)
            else:
                st.error(msg)

# -----------------------------
# My Bookings (outside the extracted-text branch)
# -----------------------------
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
st.markdown('<div class="section-title">My Bookings</div>', unsafe_allow_html=True)

all_rows = load_bookings()
if not all_rows:
    st.info("No bookings yet.")
else:
    df_display = pd.DataFrame(all_rows)
    st.dataframe(df_display, use_container_width=True)

    cancel_id_input = st.text_input("Enter Booking ID to cancel")
    if st.button("Cancel Booking"):
        if not cancel_id_input.strip():
            st.error("Please enter a Booking ID.")
        else:
            removed = cancel_booking(cancel_id_input.strip()) if 'cancel_booking' in globals() else None
            # define a local cancel if not provided
            if removed is None:
                # implement cancel here
                rows = load_bookings()
                new_rows = [r for r in rows if r.get("booking_id") != cancel_id_input.strip()]
                if len(new_rows) != len(rows):
                    save_bookings(new_rows)
                    st.success("Booking cancelled.")
                else:
                    st.error("Booking ID not found.")
            else:
                st.success("Booking cancelled.")

# Footer
st.markdown('<div class="hr"></div>', unsafe_allow_html=True)
st.caption("© 2025 — For education/information only. Not a medical device; not medical advice.")
# End of Part 6/6
