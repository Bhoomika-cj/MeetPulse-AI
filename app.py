"""
MeetMind AI — Intelligent Meeting Action-Item Extractor
=========================================================
"Turn messy meetings into organized action."

Entry point for the Streamlit application. Handles page config, session
state initialization, the transcript-submission form, the results
dashboard, the interactive data editor, filters, analytics, and exports.

Run locally:
    streamlit run app.py

Requires GEMINI_API_KEY to be set as an environment variable or in
.streamlit/secrets.toml (see README.md).
"""

from __future__ import annotations

import os
import html
import re
import hashlib
from postgrest.exceptions import APIError
from supabase import create_client, Client
import secrets
from datetime import date, datetime

import pandas as pd
import streamlit as st

try:
    from streamlit_mic_recorder import mic_recorder
except ImportError:
    mic_recorder = None

from utils.analytics import (
    action_items_to_dataframe,
    apply_filters,
    chart_deadline_distribution,
    chart_tasks_by_owner,
    chart_tasks_by_priority,
    chart_tasks_by_status,
    compute_extraction_quality,
    compute_kpis,
    compute_meeting_health,
    chart_workload_balance,
)
from utils.export import to_csv_bytes, to_json_bytes, to_markdown_report, to_text_report
from utils.gemini_client import extract_action_items, generate_meeting_intelligence, answer_meeting_question, generate_followup_email, transcribe_audio

# --------------------------------------------------------------------------
# Page config
# --------------------------------------------------------------------------

st.set_page_config(
    page_title="MeetMind AI | Meeting Action-Item Extractor",
    page_icon="🧠",
    layout="wide",
    initial_sidebar_state="expanded",
)

PRIORITY_OPTIONS = ["High", "Medium", "Low"]
STATUS_OPTIONS = ["Pending", "In Progress", "Completed", "Blocked"]

SAMPLE_TRANSCRIPTS = {
    "College Project Meeting": "data/college_project_meeting.txt",
    "Software Sprint Standup": "data/software_sprint.txt",
    "Marketing Review Meeting": "data/marketing_review.txt",
}


# --------------------------------------------------------------------------
# API key resolution (never hard-coded)
# --------------------------------------------------------------------------

def get_api_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY")
    if key:
        return key
    try:
        return st.secrets.get("GEMINI_API_KEY")  # type: ignore[union-attr]
    except Exception:
        return None


# --------------------------------------------------------------------------
# Shared workspace storage & authentication
# --------------------------------------------------------------------------
def _secret(name: str):
    try:
        return st.secrets.get(name)
    except Exception:
        return os.environ.get(name)

@st.cache_resource
def get_supabase() -> Client:
    url = _secret("SUPABASE_URL")
    key = _secret("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("Supabase is not configured. Add SUPABASE_URL and SUPABASE_KEY to Streamlit secrets.")
    return create_client(url, key)

def init_database():
    # Supabase tables are created once from supabase_schema.sql.
    return None

def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()

def generate_workspace_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    sb = get_supabase()
    for _ in range(20):
        code = "MM-" + "".join(secrets.choice(alphabet) for _ in range(6))
        existing = sb.table("workspaces").select("id").eq("code", code).limit(1).execute().data
        if not existing:
            return code
    raise RuntimeError("Could not generate a unique workspace code. Please try again.")

def create_workspace(name, workspace_name, contact, password):
    """Create the shared workspace and its first member."""
    try:
        sb = get_supabase()
        code = generate_workspace_code()
        created_at = datetime.now().isoformat()

        result = sb.table("workspaces").insert({
            "code": code,
            "name": workspace_name,
            "password_hash": hash_password(password),
            "created_at": created_at,
        }).execute()

        if not result.data:
            raise RuntimeError("Workspace insert returned no data. Check the workspaces table schema.")

        ws = result.data[0]

        member_result = sb.table("members").upsert(
            {
                "workspace_id": ws["id"],
                "name": name,
                "contact": contact,
                "created_at": created_at,
            },
            on_conflict="workspace_id,contact",
        ).execute()

        if member_result.data is None:
            raise RuntimeError("Workspace was created but the first member could not be added.")

        return ws["id"], code

    except Exception as exc:
        # Keep the original Supabase/PostgREST error visible so setup problems
        # can be fixed instead of being hidden behind a generic message.
        raise RuntimeError(f"Supabase error while creating workspace: {exc}") from exc


def join_workspace(name, contact, code, password):
    sb = get_supabase()
    rows = sb.table("workspaces").select("*").eq("code", code.strip().upper()).limit(1).execute().data
    if not rows:
        return None
    ws = rows[0]
    if ws.get("password_hash") != hash_password(password):
        return None
    sb.table("members").upsert({
        "workspace_id": ws["id"], "name": name, "contact": contact,
        "created_at": datetime.now().isoformat()
    }, on_conflict="workspace_id,contact").execute()
    return ws

def load_workspace_history(workspace_id):
    sb = get_supabase()
    rows = sb.table("meetings").select("id,title,meeting_date,participants,summary,transcript,task_count,completion_pct,created_by").eq("workspace_id", workspace_id).order("created_at", desc=True).execute().data or []
    return [{
        "id": r.get("id"), "title": r.get("title", "Untitled Meeting"), "date": r.get("meeting_date", ""),
        "participants": r.get("participants") or "", "summary": r.get("summary") or "",
        "transcript": r.get("transcript") or "", "task_count": r.get("task_count") or 0,
        "completion_pct": r.get("completion_pct") or 0, "created_by": r.get("created_by") or ""
    } for r in rows]

def load_action_items_from_workspace(meeting_id):
    if not meeting_id:
        return pd.DataFrame()
    sb = get_supabase()
    rows = sb.table("action_items").select("local_id,task,owner,deadline,priority,status,dependency,context,confidence").eq("meeting_id", meeting_id).order("created_at").execute().data or []
    if not rows:
        return pd.DataFrame()
    data = [{
        "ID": r.get("local_id", i + 1), "Task": r.get("task", ""), "Owner": r.get("owner", "Unassigned"),
        "Deadline": r.get("deadline", "Not specified"), "Priority": r.get("priority", "Medium"),
        "Status": r.get("status", "Pending"), "Dependency": r.get("dependency", "None"),
        "Context": r.get("context", ""), "Confidence": float(r.get("confidence") or 0.5)
    } for i, r in enumerate(rows)]
    return pd.DataFrame(data)

def save_action_items_to_workspace(meeting_id, df):
    """Persist the current meeting's task statuses so every workspace member sees them."""
    if not meeting_id or not st.session_state.get("workspace_id"):
        return
    sb = get_supabase()
    # Replace only this meeting's rows; other meetings in the shared workspace are untouched.
    sb.table("action_items").delete().eq("meeting_id", meeting_id).execute()
    if df is None or df.empty:
        return
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "workspace_id": st.session_state.workspace_id,
            "meeting_id": meeting_id,
            "local_id": int(r.get("ID", 0)) if str(r.get("ID", "")).strip() else 0,
            "task": str(r.get("Task", "")), "owner": str(r.get("Owner", "Unassigned")),
            "deadline": str(r.get("Deadline", "Not specified")), "priority": str(r.get("Priority", "Medium")),
            "status": str(r.get("Status", "Pending")), "dependency": str(r.get("Dependency", "None")),
            "context": str(r.get("Context", "")), "confidence": float(r.get("Confidence", 0.5) or 0.5),
            "updated_by": st.session_state.get("user_name", ""), "updated_at": datetime.now().isoformat(),
        })
    if rows:
        sb.table("action_items").insert(rows).execute()

def save_meeting_to_workspace(entry):
    if not st.session_state.get("workspace_id"):
        return None
    sb = get_supabase()
    result = sb.table("meetings").insert({
        "workspace_id": st.session_state.workspace_id,
        "title": entry["title"], "meeting_date": entry["date"],
        "participants": entry.get("participants", ""),
        "summary": entry.get("summary", ""),
        "transcript": entry.get("transcript", ""),
        "task_count": int(entry.get("task_count", 0)),
        "completion_pct": float(entry.get("completion_pct", 0)),
        "created_by": st.session_state.get("user_name", ""),
        "created_at": datetime.now().isoformat()
    }).execute()
    return result.data[0]["id"] if result.data else None

def render_auth_gate():
    st.markdown("""<style>
    .auth-wrap{max-width:1120px;margin:5vh auto;padding:0 20px}.auth-hero{padding:46px;border-radius:30px;background:linear-gradient(135deg,#151d31,#0e1422);border:1px solid #2b3855}.auth-brand{font-size:2.2rem;font-weight:850}.auth-sub{color:#9ba9c0;font-size:1.05rem;margin-top:8px}.auth-card{background:#171f2d;border:1px solid #2a3750;border-radius:26px;padding:24px}
    </style>""", unsafe_allow_html=True)
    st.markdown('<div class="auth-wrap"><div class="auth-hero"><div class="auth-brand">🧠 MeetMind AI</div><div class="auth-sub">Your shared AI meeting workspace. Create a private workspace or join your team using a workspace code and shared password.</div></div></div>', unsafe_allow_html=True)
    left,right=st.columns(2, gap="large")
    with left:
        st.markdown('<div class="auth-card">', unsafe_allow_html=True)
        st.subheader("Create a workspace")
        st.caption("Create one shared workspace, then give your team the code and password.")
        with st.form("create_workspace_form"):
            name=st.text_input("Your name", key="create_name")
            contact=st.text_input("Email or phone number", key="create_contact")
            ws_name=st.text_input("Workspace / project name", key="create_ws")
            password=st.text_input("Create shared workspace password", type="password", key="create_pw")
            confirm=st.text_input("Confirm password", type="password", key="confirm_pw")
            submitted=st.form_submit_button("Create Workspace", use_container_width=True)
        if submitted:
            if not all([name.strip(),contact.strip(),ws_name.strip(),password]): st.error("Please complete every field.")
            elif password!=confirm: st.error("Passwords do not match.")
            elif len(password)<6: st.error("Use a password with at least 6 characters.")
            else:
                try:
                    wid,code=create_workspace(name.strip(),ws_name.strip(),contact.strip(),password)
                    st.session_state.update({"authenticated":True,"workspace_id":wid,"workspace_code":code,"workspace_name":ws_name.strip(),"user_name":name.strip(),"user_contact":contact.strip(),"auth_version":AUTH_SESSION_VERSION,"meeting_history":[],"main_navigation":"🏠  Dashboard"})
                    st.success(f"Workspace created! Your team code is {code}. Share the code and password with your team.")
                    st.rerun()
                except Exception as exc:
                    st.error("Could not create the workspace. Please check that the Supabase database schema has been run.")
                    with st.expander("Show database error details"):
                        st.code(str(exc))
                    st.info("In Supabase, open SQL Editor → New query, run the complete supabase_schema.sql file, then try again.")
        st.markdown('</div>', unsafe_allow_html=True)
    with right:
        st.markdown('<div class="auth-card">', unsafe_allow_html=True)
        st.subheader("Join a workspace")
        st.caption("Use the workspace code and shared password provided by your team.")
        with st.form("join_workspace_form"):
            name=st.text_input("Your name", key="join_name")
            contact=st.text_input("Email or phone number", key="join_contact")
            code=st.text_input("Workspace code", placeholder="MM-ABC123", key="join_code")
            password=st.text_input("Shared workspace password", type="password", key="join_pw")
            joined=st.form_submit_button("Join Workspace", use_container_width=True)
        if joined:
            if not all([name.strip(),contact.strip(),code.strip(),password]): st.error("Please complete every field.")
            else:
                ws=join_workspace(name.strip(),contact.strip(),code.strip(),password)
                if not ws: st.error("Workspace code or shared password is incorrect.")
                else:
                    st.session_state.update({"authenticated":True,"workspace_id":ws["id"],"workspace_code":ws["code"],"workspace_name":ws["name"],"user_name":name.strip(),"user_contact":contact.strip(),"auth_version":AUTH_SESSION_VERSION,"meeting_history":load_workspace_history(ws["id"]),"current_meeting_id":None,"main_navigation":"🏠  Dashboard"})
                    st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)

init_database()

# --------------------------------------------------------------------------
# Authentication session helpers
# --------------------------------------------------------------------------
AUTH_SESSION_VERSION = "login_v3"

def reset_auth_session() -> None:
    """Clear the active workspace session and return to the login screen."""
    auth_keys = [
        "authenticated", "workspace_id", "workspace_code", "workspace_name",
        "user_name", "user_contact", "meeting_history", "has_results",
        "main_navigation", "portal_theme",
    ]
    for key in auth_keys:
        st.session_state.pop(key, None)
    # Explicitly set the guard to False so a stale Streamlit session cannot
    # fall through to the dashboard.
    st.session_state.authenticated = False
    st.session_state.auth_version = AUTH_SESSION_VERSION


def has_valid_auth_session() -> bool:
    """Dashboard access requires a real successful workspace login/join."""
    return bool(
        st.session_state.get("authenticated")
        and st.session_state.get("workspace_id")
        and st.session_state.get("workspace_code")
        and st.session_state.get("workspace_name")
        and st.session_state.get("user_name")
        and st.session_state.get("user_contact")
        and st.session_state.get("auth_version") == AUTH_SESSION_VERSION
    )

# --------------------------------------------------------------------------
# Session state initialization
# --------------------------------------------------------------------------

def init_session_state() -> None:
    defaults = {
        "transcript_text": "",
        "meeting_title": "",
        "meeting_date": date.today(),
        "team_project": "",
        "participants": "",
        "meeting_summary": "",
        "key_decisions": [],
        "risks_and_blockers": [],
        "follow_up_questions": [],
        "action_items_df": pd.DataFrame(),
        "has_results": False,
        "meeting_history": [],
        "current_meeting_id": None,
        "last_error": None,
        "filter_search": "",
        "filter_owner": "All",
        "filter_priority": "All",
        "filter_status": "All",
        "filter_overdue_only": False,
        "meeting_intelligence": {},
        "qa_history": [],
        "followup_email": "",
        "theme_name": "Midnight",
        "theme_accent": "#7C3AED",
        "history_query": "",
        "history_answer": "",
        "authenticated": False,
        "workspace_id": None,
        "workspace_code": "",
        "workspace_name": "",
        "user_name": "",
        "user_contact": "",
        "auth_version": AUTH_SESSION_VERSION,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


init_session_state()

# Force logout of stale browser sessions created by older versions of the app.
if st.session_state.get("auth_version") != AUTH_SESSION_VERSION:
    reset_auth_session()

# IMPORTANT: never open the dashboard until a real workspace login/create/join
# has completed successfully. If a browser has a stale or incomplete session,
# send the user back to the login screen.
if not has_valid_auth_session():
    st.session_state.authenticated = False
    render_auth_gate()
    st.stop()

if st.session_state.workspace_id and not st.session_state.meeting_history:
    st.session_state.meeting_history = load_workspace_history(st.session_state.workspace_id)
if st.session_state.workspace_id and st.session_state.meeting_history and not st.session_state.get("current_meeting_id"):
    latest = st.session_state.meeting_history[0]
    st.session_state.current_meeting_id = latest.get("id")
    loaded_tasks = load_action_items_from_workspace(latest.get("id"))
    if not loaded_tasks.empty:
        st.session_state.action_items_df = loaded_tasks
        st.session_state.has_results = True


# --------------------------------------------------------------------------
# Theme Studio
# --------------------------------------------------------------------------
THEMES = {
    "Midnight": ("#0B1020", "#111827", "#7C3AED", "#E5E7EB", "#A78BFA"),
    "Cyberpunk": ("#09020F", "#1B0B2E", "#FF00CC", "#F5F3FF", "#00F5D4"),
    "Ocean": ("#071A2D", "#0B2A3D", "#06B6D4", "#E0F7FF", "#38BDF8"),
    "Sakura": ("#2A1220", "#3A1A2B", "#F472B6", "#FFF1F7", "#C084FC"),
    "Forest": ("#0B1F16", "#123524", "#22C55E", "#ECFDF5", "#86EFAC"),
    "Sunset": ("#24120A", "#3B1C10", "#F97316", "#FFF7ED", "#FB7185"),
    "Terminal": ("#020805", "#07130B", "#22C55E", "#DCFCE7", "#84CC16"),
    "Minimal": ("#F8FAFC", "#FFFFFF", "#2563EB", "#0F172A", "#64748B"),
    "Glassmorphism": ("#0F172A", "rgba(30,41,59,0.72)", "#60A5FA", "#F8FAFC", "#A78BFA"),
}

def apply_theme(name: str, accent: str) -> None:
    bg, card, primary, text, secondary = THEMES.get(name, THEMES["Midnight"])
    if name == "Custom Accent":
        primary = accent
        bg, card, text, secondary = "#0F172A", "#111827", "#F8FAFC", accent
    st.markdown(f"""<style>
    .stApp {{ background: {bg}; color: {text}; }}
    [data-testid='stSidebar'] {{ background: {card}; }}
    .mm-hero {{ background: linear-gradient(135deg, {card} 0%, {bg} 100%) !important; color:{text} !important; border:1px solid {primary}; }}
    .mm-hero p {{ color:{secondary} !important; }}
    .mm-badge {{ background:{primary} !important; color:white !important; }}
    div[data-testid='stMetric'] {{ background:{card} !important; border-color:{primary} !important; color:{text} !important; }}
    div[data-testid='stMetricValue'] {{ color:{text} !important; }}
    .stButton > button {{ border-color:{primary}; color:{text}; }}
    .stButton > button:hover {{ border-color:{secondary}; box-shadow:0 0 14px {primary}55; }}
    </style>""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("""
    <div class="portal-brand">
      <div class="brand-mark">M</div>
      <div><div class="brand-name">MeetMind</div><div class="brand-sub">AI MEETING PORTAL · NAV FIX v3</div></div>
    </div>
    """, unsafe_allow_html=True)
    st.caption(f"👤 {st.session_state.user_name} · {st.session_state.workspace_name}")
    st.caption(f"Workspace: `{st.session_state.workspace_code}`")
    if st.button("Sign out", use_container_width=True, key="sidebar_signout"):
        reset_auth_session()
        st.rerun()
    st.markdown('<div class="nav-label">OVERVIEW</div>', unsafe_allow_html=True)
    page = st.radio(
        "Navigate",
        ["🏠  Dashboard", "🎙️  Analyze Meeting", "📋  Action Items", "🧠  AI Intelligence", "🗂️  Meeting History"],
        label_visibility="collapsed",
        key="main_navigation",
    )
    st.markdown('<div class="nav-label">PERSONALIZE</div>', unsafe_allow_html=True)
    selected_theme = st.selectbox("Theme", list(THEMES.keys()), index=list(THEMES.keys()).index("Midnight"), key="portal_theme")
    st.session_state.theme_name = selected_theme
    st.session_state.theme_accent = THEMES[selected_theme][2]
    st.divider()
    st.markdown('<div class="portal-tip"><b>✨ MeetMind Workspace</b><br><span>Turn every meeting into clear action.</span></div>', unsafe_allow_html=True)

apply_theme(st.session_state.theme_name, st.session_state.theme_accent)

# --------------------------------------------------------------------------
# Custom styling
# --------------------------------------------------------------------------

st.markdown("""
<style>
/* ============================================================
   MeetMind AI — Professional Full-Screen Dark Portal
   ============================================================ */
:root{
  --bg:#080d18;
  --bg2:#0b1120;
  --panel:#111827;
  --panel2:#151d2b;
  --border:#26344d;
  --text:#edf2fb;
  --muted:#9aa8bd;
  --accent:#7c5cff;
  --accent2:#9b7bff;
}

/* Remove Streamlit's white top/header area completely */
html, body, .stApp { background:var(--bg)!important; color:var(--text)!important; }
/* Keep the Streamlit header transparent so the sidebar reopen button is always available. */
header[data-testid="stHeader"]{
  display:flex!important;
  background:transparent!important;
  height:0!important;
  min-height:0!important;
  z-index:999999!important;
}
[data-testid="stToolbar"],
[data-testid="stDecoration"],
#MainMenu,
footer { display:none!important; }

/* FIX: Keep MeetMind navigation permanently visible. Do not rely on Streamlit's
   collapsible sidebar control, because its reopen control differs by Streamlit version. */
[data-testid="stSidebar"],
[data-testid="stSidebar"][aria-expanded="false"]{
  transform:translateX(0)!important;
  margin-left:0!important;
  visibility:visible!important;
  min-width:270px!important;
  width:270px!important;
}
[data-testid="stSidebar"] > div:first-child{
  transform:translateX(0)!important;
  margin-left:0!important;
  visibility:visible!important;
}
[data-testid="stSidebarCollapseButton"],
[data-testid="stSidebarCollapsedControl"],
button[data-testid="stSidebarCollapseButton"],
button[data-testid="stSidebarCollapsedControl"]{
  display:none!important;
}
[data-testid="stAppViewContainer"] .main{
  margin-left:0!important;
}

/* Do not let the hidden header create a white strip above the application. */
header[data-testid="stHeader"] *{ background:transparent!important; }


[data-testid="stAppViewContainer"]{
  background:
    radial-gradient(circle at 65% 0%, rgba(91,72,191,.08), transparent 30%),
    var(--bg)!important;
}
[data-testid="stAppViewContainer"] > .main{
  background:transparent!important;
}
[data-testid="stAppViewContainer"] .main .block-container,
.main .block-container{
  max-width:none!important;
  padding:1.75rem 2.55rem 3rem!important;
}
[data-testid="stAppViewContainer"] .main{
  padding-top:0!important;
}

/* Sidebar */
[data-testid="stSidebar"]{
  width:320px!important;
  min-width:320px!important;
  background:linear-gradient(180deg,#0d1421 0%,#0b111c 100%)!important;
  border-right:1px solid #26324a!important;
}
[data-testid="stSidebarContent"]{
  padding:1.65rem 1.05rem 2rem!important;
}
[data-testid="stSidebarNav"]{display:none!important}

.portal-brand{
  display:flex;align-items:center;gap:13px;
  padding:0.2rem 0.55rem 2rem;
}
.brand-mark{
  width:48px;height:48px;border-radius:16px;
  display:grid;place-items:center;
  background:linear-gradient(135deg,#705cff,#b276ff);
  box-shadow:0 8px 24px rgba(112,92,255,.28);
  font-weight:900;color:#fff;font-size:1.22rem;
}
.brand-name{font-weight:850;font-size:1.28rem;color:#f8faff}
.brand-sub{
  font-size:.68rem;letter-spacing:.18em;color:#9aa8bd;
  font-weight:800;margin-top:4px;
}
.nav-label{
  font-size:.72rem;letter-spacing:.17em;color:#8e9ab0;
  font-weight:850;padding:0.65rem 0.7rem 0.6rem;
}

/* Navigation */
[data-testid="stSidebar"] .stRadio > div{gap:5px!important}
[data-testid="stSidebar"] .stRadio label{
  min-height:48px!important;
  padding:12px 14px!important;
  border-radius:13px!important;
  margin:1px 0!important;
  color:#c6cfde!important;
  font-weight:650!important;
  transition:all .2s ease;
}
[data-testid="stSidebar"] .stRadio label:hover{
  background:#171f2e!important;
}
[data-testid="stSidebar"] .stRadio label:has(input:checked),
[data-testid="stSidebar"] .stRadio [aria-checked="true"]{
  background:linear-gradient(90deg,rgba(102,85,220,.26),rgba(130,87,200,.22))!important;
  border:1px solid rgba(129,112,242,.36)!important;
  color:#fff!important;
  box-shadow:inset 3px 0 0 var(--accent);
}

/* Hide default radio circles for app-style nav */
[data-testid="stSidebar"] .stRadio input[type="radio"]{display:none!important}
[data-testid="stSidebar"] .stRadio label > div:first-child{display:none!important}

.portal-tip{
  margin-top:1.35rem;padding:18px;border-radius:18px;
  background:linear-gradient(135deg,rgba(29,49,75,.95),rgba(50,34,85,.95));
  border:1px solid #3b4165;color:#e2e8f5;
}
.portal-tip b{font-size:1rem}
.portal-tip span{display:block;color:#a9b5c7;margin-top:7px;font-size:.92rem;line-height:1.5}

/* Main page */
.portal-top{
  display:flex;justify-content:space-between;align-items:center;
  gap:2rem;margin:0 0 1.8rem;
}
.portal-kicker{
  font-size:.72rem;letter-spacing:.2em;color:#9b91dd;
  font-weight:850;margin-bottom:.45rem;
}
.portal-title{
  font-size:2.7rem;font-weight:850;letter-spacing:-.035em;
  line-height:1.1;color:#f4f6fb;margin:0 0 .55rem;
}
.section-muted{color:var(--muted);margin-top:.25rem;font-size:1rem;line-height:1.6}
.portal-date{
  white-space:nowrap;padding:12px 22px;border-radius:999px;
  background:#151e2d;border:1px solid #2c3a54;
  color:#dfe7f5;font-weight:750;
}

/* Cards */
.section-card,.mm-hero{
  background:linear-gradient(145deg,rgba(20,29,44,.96),rgba(15,23,37,.96))!important;
  border:1px solid var(--border)!important;
  border-radius:18px!important;
  padding:1.45rem!important;
  box-shadow:0 12px 34px rgba(0,0,0,.16);
}
.section-card h3{margin:0;color:#f0f3fa}
.mm-hero{margin-bottom:1.25rem!important}
.mm-badge{
  background:linear-gradient(135deg,#6d5be8,#8c60e8)!important;
  color:white!important;
}

/* Metrics */
[data-testid="stMetric"]{
  background:linear-gradient(145deg,#151d2a,#111827)!important;
  border:1px solid #2b3954!important;
  border-radius:18px!important;
  padding:1.35rem!important;
  min-height:142px;
  box-shadow:0 10px 25px rgba(0,0,0,.13)!important;
}
[data-testid="stMetricLabel"]{
  color:#9eabc0!important;font-weight:800!important;
  letter-spacing:.08em!important;text-transform:uppercase;
}
[data-testid="stMetricValue"]{
  color:#f5f7fc!important;font-size:2rem!important;font-weight:850!important;
}

/* Inputs — prevent white boxes */
.stTextInput input,
.stTextArea textarea,
.stDateInput input,
.stNumberInput input,
[data-baseweb="input"] input,
[data-baseweb="textarea"] textarea{
  background:#111827!important;
  color:#edf2fb!important;
  border:1px solid #2b3952!important;
  border-radius:12px!important;
  box-shadow:none!important;
}
.stTextInput input::placeholder,
.stTextArea textarea::placeholder{color:#7f8ba0!important}
.stTextInput input:focus,
.stTextArea textarea:focus,
.stDateInput input:focus{
  border-color:#7b68ee!important;
  box-shadow:0 0 0 3px rgba(124,92,255,.12)!important;
}
[data-baseweb="select"] > div,
[data-baseweb="select"] > div > div{
  background:#111827!important;
  color:#edf2fb!important;
  border-color:#2b3952!important;
}

/* File uploader */
[data-testid="stFileUploader"] section{
  background:#111827!important;
  border:1px dashed #5d61a7!important;
  border-radius:16px!important;
  padding:1.2rem!important;
}
[data-testid="stFileUploaderDropzone"]{
  background:#111827!important;
  color:#cbd5e1!important;
}
[data-testid="stFileUploaderDropzone"] button{
  background:#1c2638!important;
  color:#edf2fb!important;
  border:1px solid #3a4962!important;
}

/* Expanders */
[data-testid="stExpander"]{
  background:#111827!important;
  border:1px solid #26344d!important;
  border-radius:15px!important;
  overflow:hidden!important;
}
[data-testid="stExpander"] summary{
  color:#e7ecf6!important;
  font-weight:700!important;
}

/* Buttons */
.stButton>button,.stDownloadButton>button{
  min-height:44px!important;
  border-radius:12px!important;
  background:linear-gradient(135deg,#6f5ee8,#8c61eb)!important;
  color:#fff!important;
  border:1px solid #947cff!important;
  font-weight:750!important;
  box-shadow:0 8px 20px rgba(93,74,200,.18)!important;
}
.stButton>button:hover,.stDownloadButton>button:hover{
  filter:brightness(1.08)!important;
  transform:translateY(-1px);
}

/* Tables, charts, tabs */
div[data-testid="stDataFrame"],
[data-testid="stPlotlyChart"]{
  background:#111827!important;
  border:1px solid #26344d!important;
  border-radius:18px!important;
  overflow:hidden!important;
}
.stTabs [data-baseweb="tab-list"]{gap:7px}
.stTabs [data-baseweb="tab"]{
  background:#111827!important;
  color:#aeb9ca!important;
  border-radius:10px 10px 0 0!important;
}
.stTabs [aria-selected="true"]{
  color:#f2f5fb!important;
  border-bottom:2px solid #856eff!important;
}

/* Status and dividers */
hr{border-color:#26344d!important}
[data-testid="stAlert"]{border-radius:14px!important}
[data-testid="stSuccess"]{
  background:rgba(13,91,63,.22)!important;
  border:1px solid rgba(53,188,126,.24)!important;
  color:#93dfb5!important;
}

/* =========================================
   RESPONSIVE SIDEBAR — NO CONTENT OVERLAP
   ========================================= */

/* Desktop */
@media (min-width: 1051px){
  [data-testid="stSidebar"]{
    width:270px!important;
    min-width:270px!important;
  }

  [data-testid="stAppViewContainer"] .main .block-container{
    max-width:100%!important;
  }
}

/* Tablet and Mobile */
@media (max-width:1050px){

  /* Sidebar becomes a proper mobile drawer */
  [data-testid="stSidebar"]{
    width:min(78vw, 320px)!important;
    min-width:min(78vw, 320px)!important;
    max-width:320px!important;
  }

  /* Dashboard uses the full available screen */
  [data-testid="stAppViewContainer"] .main{
    width:100%!important;
    margin-left:0!important;
  }

  [data-testid="stAppViewContainer"] .main .block-container{
    width:100%!important;
    max-width:100%!important;
    padding:1rem!important;
    overflow-x:hidden!important;
  }

  .portal-title{
    font-size:1.7rem!important;
  }

  .portal-date{
    display:none!important;
  }
}

/* Small Mobile Phones */
@media (max-width:600px){

  [data-testid="stSidebar"]{
    width:85vw!important;
    min-width:85vw!important;
    max-width:320px!important;
  }

  [data-testid="stAppViewContainer"] .main .block-container{
    padding:0.8rem!important;
  }

  .portal-title{
    font-size:1.45rem!important;
  }
}
</style>
""", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Sidebar — meeting history + API status
# --------------------------------------------------------------------------

with st.sidebar:
    st.subheader("⚙️ System Status")
    api_key = get_api_key()
    if api_key:
        st.success("Gemini API key detected.")
    else:
        st.error("No Gemini API key found.")
        st.caption("Set GEMINI_API_KEY as an environment variable, or add it to `.streamlit/secrets.toml`.")

    st.divider()
    st.subheader("🕘 Meeting History")
    if st.session_state.meeting_history:
        for entry in reversed(st.session_state.meeting_history[-10:]):
            st.markdown(
                f"**{entry['title']}**  \n"
                f"{entry['date']} · {entry['task_count']} tasks · "
                f"{entry['completion_pct']}% complete"
            )
            st.divider()
    else:
        st.caption("No meetings processed yet this session. "
                    "Shared workspace history is stored securely in your cloud workspace.")


if page == "🎙️  Analyze Meeting":
    # --------------------------------------------------------------------------
    # Section 1 — Transcript submission form
    # --------------------------------------------------------------------------

    st.markdown("<div class=\"portal-kicker\">ANALYZE A MEETING</div><div class=\"portal-title\">Meeting Analyzer</div><div class=\"section-muted\">Upload, record, or paste a transcript to generate decisions and action items.</div>", unsafe_allow_html=True)
    st.markdown("<div class=\"section-card\">", unsafe_allow_html=True)

    with st.expander("💡 Try a sample transcript first", expanded=False):
        sample_choice = st.selectbox(
            "Sample meetings",
            options=["None"] + list(SAMPLE_TRANSCRIPTS.keys()),
            key="sample_choice",
        )
        if sample_choice != "None" and st.button("Load sample into the form below"):
            sample_path = SAMPLE_TRANSCRIPTS[sample_choice]
            try:
                with open(sample_path, "r", encoding="utf-8") as f:
                    st.session_state["prefill_transcript"] = f.read()
                st.session_state["prefill_title"] = sample_choice
                st.rerun()
            except FileNotFoundError:
                st.warning("Sample file not found on disk.")

    with st.form("meeting_form", clear_on_submit=False):
        # Compact three-column row so the main details are visible immediately.
        col1, col2, col3 = st.columns(3)
        with col1:
            meeting_title = st.text_input(
                "📋 Meeting Title",
                value=st.session_state.pop("prefill_title", st.session_state.meeting_title),
                placeholder="e.g. Weekly Sprint Review",
            )
        with col2:
            team_project = st.text_input(
                "👥 Team / Project",
                value=st.session_state.team_project,
                placeholder="e.g. MeetMind Product Team",
            )
        with col3:
            meeting_date_input = st.date_input("📅 Meeting Date", value=st.session_state.meeting_date)

        col4, col5 = st.columns([2, 1])
        with col4:
            participants = st.text_input(
                "👤 Participants (comma-separated)",
                value=st.session_state.participants,
                placeholder="Bhoomi, Ananya, Rahul, Kiran, Priya",
            )
        with col5:
            uploaded_file = st.file_uploader("📄 Upload .txt", type=["txt"])

        st.markdown("#### 🎙️ Voice Meeting Input")
        st.caption("Record a meeting in your browser or upload audio. Gemini will convert it into an editable transcript.")
        audio_tab1, audio_tab2 = st.tabs(["🎤 Record voice", "📁 Upload audio"])
        recorded_audio = None
        uploaded_audio = None
        with audio_tab1:
            if mic_recorder is not None:
                recorded_audio = mic_recorder(start_prompt="⏺️ Start recording", stop_prompt="⏹️ Stop recording", just_once=False, use_container_width=True, key="meeting_voice_recorder")
            else:
                st.warning("Voice recorder is unavailable. Run: pip install streamlit-mic-recorder")
        with audio_tab2:
            uploaded_audio = st.file_uploader("Upload meeting audio", type=["wav", "mp3", "m4a", "ogg", "aac", "flac"], key="meeting_audio_upload")

        transcript_default = st.session_state.pop("prefill_transcript", st.session_state.transcript_text)
        transcript_input = st.text_area(
            "Paste raw meeting transcript",
            value=transcript_default,
            height=190,
            placeholder="Paste the raw, messy meeting transcript here...",
        )

        submitted = st.form_submit_button("🤖 Extract Action Items", use_container_width=True)

    if submitted:
        final_transcript = transcript_input

        audio_bytes = None
        audio_mime = "audio/wav"
        if recorded_audio and recorded_audio.get("bytes"):
            audio_bytes = recorded_audio["bytes"]
            audio_mime = recorded_audio.get("mime_type") or "audio/wav"
        elif uploaded_audio is not None:
            audio_bytes = uploaded_audio.getvalue()
            audio_mime = uploaded_audio.type or "audio/wav"

        if audio_bytes:
            with st.spinner("🎙️ Transcribing your meeting audio with Gemini..."):
                transcription_result = transcribe_audio(audio_bytes, audio_mime, api_key)
            if transcription_result.success:
                final_transcript = transcription_result.data["transcript"]
                st.session_state.transcript_text = final_transcript
                st.success("Voice transcription completed. The transcript below is now ready for analysis.")
                with st.expander("📝 View generated transcript", expanded=True):
                    st.text_area("Generated voice transcript", value=final_transcript, height=220, key="generated_transcript_preview")
            else:
                st.error(transcription_result.error)

        if uploaded_file is not None:
            try:
                final_transcript = uploaded_file.read().decode("utf-8")
            except UnicodeDecodeError:
                st.error("Could not read the uploaded file as UTF-8 text.")
                final_transcript = transcript_input

        st.session_state.meeting_title = meeting_title
        st.session_state.meeting_date = meeting_date_input
        st.session_state.team_project = team_project
        st.session_state.participants = participants
        st.session_state.transcript_text = final_transcript

        if not final_transcript or not final_transcript.strip():
            st.session_state.last_error = "Please paste a transcript or upload a .txt file before extracting."
            st.session_state.has_results = False
        else:
            with st.spinner("MeetMind AI is analyzing the transcript..."):
                result = extract_action_items(
                    meeting_title=meeting_title,
                    meeting_date=str(meeting_date_input),
                    team_project=team_project,
                    participants=participants,
                    transcript=final_transcript,
                    api_key=api_key,
                )

            if not result.success:
                st.session_state.last_error = result.error
                st.session_state.has_results = False
            else:
                st.session_state.last_error = None
                st.session_state.meeting_summary = result.data["meeting_summary"]
                st.session_state.key_decisions = result.data["key_decisions"]
                st.session_state.risks_and_blockers = result.data["risks_and_blockers"]
                st.session_state.follow_up_questions = result.data["follow_up_questions"]
                df = action_items_to_dataframe(result.data["action_items"])
                st.session_state.action_items_df = df
                st.session_state.has_results = True

                kpis = compute_kpis(df)
                meeting_entry = {
                    "title": meeting_title or "Untitled Meeting",
                    "date": str(meeting_date_input),
                    "participants": participants,
                    "summary": st.session_state.meeting_summary,
                    "transcript": final_transcript,
                    "task_count": kpis["total"],
                    "completion_pct": kpis["completion_pct"],
                    "created_by": st.session_state.user_name,
                }
                meeting_id = save_meeting_to_workspace(meeting_entry)
                meeting_entry["id"] = meeting_id
                st.session_state.current_meeting_id = meeting_id
                st.session_state.meeting_history.append(meeting_entry)
                if meeting_id:
                    save_action_items_to_workspace(meeting_id, df)

    if st.session_state.last_error:
        st.error(st.session_state.last_error)
    st.markdown("</div>", unsafe_allow_html=True)


# --------------------------------------------------------------------------
# Empty state
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Shared action-items dataframe
# --------------------------------------------------------------------------
# Keep df available to every page (Dashboard, Search, Action Items, AI, etc.)
# so navigation never raises NameError when switching directly to a page.
df = st.session_state.get("action_items_df")
if df is None:
    df = pd.DataFrame(columns=["ID", "Task", "Owner", "Priority", "Due Date", "Status", "Confidence"])

if not st.session_state.has_results and page not in ["🎙️ Analyze Meeting", "🏠 Dashboard"]:
    st.info("No meeting has been analyzed yet. You can still explore this page; analyze a meeting to see data here.")


if page == "🏠  Dashboard":
    df = st.session_state.action_items_df
    kpis = compute_kpis(df)
    meetings = len(st.session_state.meeting_history)
    pending = int((df["Status"] == "Pending").sum()) if not df.empty and "Status" in df.columns else 0
    completed = int((df["Status"] == "Completed").sum()) if not df.empty and "Status" in df.columns else 0
    display_name = html.escape(str(st.session_state.get("user_name") or "there"))
    st.markdown('<div class="portal-top"><div><div class="portal-kicker">MEETING INTELLIGENCE</div><div class="portal-title">Welcome back, '+display_name+'</div></div><div class="portal-date">📅 '+datetime.now().strftime('%A, %d %B %Y')+'</div></div>', unsafe_allow_html=True)
    a,b,c,d = st.columns(4)
    a.metric("MEETINGS", meetings, "Analyzed sessions")
    b.metric("PENDING", pending, "Awaiting completion")
    c.metric("COMPLETED", completed, "Across action items")
    d.metric("ACTION ITEMS", kpis["total"], "Tasks extracted")
    left,right=st.columns([1.65,1])
    with left:
        st.markdown('<div class="section-card"><h3>Recent meetings</h3><div class="section-muted">Your latest analyzed meeting activity</div>', unsafe_allow_html=True)
        if st.session_state.meeting_history:
            for h in reversed(st.session_state.meeting_history[-5:]):
                st.markdown(f"**{h['title']}**")
                st.caption(f"{h['date']} · {h['task_count']} action items · {h['completion_pct']}% complete")
                st.divider()
        else:
            st.markdown('**No meetings analyzed yet**')
            st.caption('Open Analyze Meeting from the sidebar to process your first meeting.')
        st.markdown('</div>', unsafe_allow_html=True)
    with right:
        st.markdown('<div class="section-card"><h3>AI meeting health</h3><div class="section-muted">Current meeting quality snapshot</div>', unsafe_allow_html=True)
        if not df.empty:
            health=compute_meeting_health(df)
            st.metric("HEALTH SCORE", f"{health['score']}/100")
            st.write(f"**Status:** {health['label']}")
        else:
            st.metric("HEALTH SCORE", "—")
            st.caption('Analyze a meeting to unlock AI health insights.')
        st.markdown('</div>', unsafe_allow_html=True)
    bottom1,bottom2=st.columns([1.45,1])
    with bottom1:
        st.markdown('<div class="section-card"><h3>Upcoming deadlines</h3><div class="section-muted">Tasks that need attention</div>', unsafe_allow_html=True)
        if not df.empty and "Deadline" in df.columns:
            show=df[[c for c in ["Task","Owner","Deadline","Priority"] if c in df.columns]].head(6)
            st.dataframe(show,use_container_width=True,hide_index=True)
        else: st.caption('No action-item deadlines available yet.')
        st.markdown('</div>', unsafe_allow_html=True)
    with bottom2:
        st.markdown('<div class="section-card"><h3>Quick access</h3><div class="section-muted">Continue where you need to</div>', unsafe_allow_html=True)
        st.info("🎙️ Analyze a meeting\n\n📋 Review action items\n\n🧠 Ask the AI assistant")
        st.markdown('</div>', unsafe_allow_html=True)


if page == "🎙️  Analyze Meeting":
    # --------------------------------------------------------------------------
    # Section 3 — Meeting Brief
    # --------------------------------------------------------------------------

    st.header("📝 Meeting Brief")

    with st.expander("Executive Summary", expanded=True):
        st.write(st.session_state.meeting_summary or "No summary available.")

    col_a, col_b = st.columns(2)
    with col_a:
        with st.expander("✅ Key Decisions", expanded=True):
            if st.session_state.key_decisions:
                for d in st.session_state.key_decisions:
                    st.markdown(f"- {d}")
            else:
                st.caption("No decisions were recorded in this transcript.")

        with st.expander("⚠️ Risks / Blockers"):
            if st.session_state.risks_and_blockers:
                for r in st.session_state.risks_and_blockers:
                    st.markdown(f"- {r}")
            else:
                st.caption("No risks or blockers were raised.")

    with col_b:
        with st.expander("❓ Follow-up Questions"):
            if st.session_state.follow_up_questions:
                for q in st.session_state.follow_up_questions:
                    st.markdown(f"- {q}")
            else:
                st.caption("No open follow-up questions.")

        with st.expander("🔎 AI Extraction Quality", expanded=True):
            quality = compute_extraction_quality(df)
            qc1, qc2 = st.columns(2)
            qc1.metric("Tasks extracted", quality["count"])
            qc2.metric("Avg. AI-estimated confidence", quality["avg_confidence"])
            qc3, qc4 = st.columns(2)
            qc3.metric("Unassigned tasks", quality["unassigned"])
            qc4.metric("Tasks without deadlines", quality["no_deadline"])
            st.caption(
                f"⚠️ {quality['ambiguous']} item(s) flagged as potentially ambiguous "
                "(AI confidence below 0.5). Confidence is an AI self-estimate, not a "
                "mathematically verified accuracy score."
            )




if page == "🧠  AI Intelligence":
    # --------------------------------------------------------------------------
    # Section 4 — AI Intelligence Center
    # --------------------------------------------------------------------------
    st.header("🧠 AI Intelligence Center")
    intel_tab, qa_tab, email_tab = st.tabs(["📈 Meeting Health", "💬 Ask the Meeting", "📧 Follow-up Email"])
    with intel_tab:
        health=compute_meeting_health(df)
        a,b,c=st.columns(3); a.metric("Meeting Health",f"{health['score']}/100"); b.metric("Status",health['label']); c.metric("High Priority",int((df['Priority']=='High').sum()) if not df.empty else 0)
        if st.button("✨ Generate AI Meeting Intelligence",use_container_width=True):
            with st.spinner("Gemini is evaluating the meeting..."):
                r=generate_meeting_intelligence(st.session_state.transcript_text,st.session_state.meeting_title,api_key)
            if r.success: st.session_state.meeting_intelligence=r.data
            else: st.warning(r.error)
        ai=st.session_state.meeting_intelligence
        if ai:
            x,y,z=st.columns(3); x.metric("AI Effectiveness",f"{ai.get('effectiveness_score','—')}/100"); y.metric("Sentiment",ai.get('sentiment','—')); z.metric("Risk",ai.get('risk_level','—'))
            st.info(ai.get('one_line_verdict',''))
            c1,c2,c3=st.columns(3)
            for col,title,key in [(c1,'Strengths','strengths'),(c2,'Improve Next Time','improvements'),(c3,'Next Meeting Agenda','next_meeting_agenda')]:
                with col:
                    st.subheader(title)
                    for item in ai.get(key,[]): st.write(f"• {item}")
        st.plotly_chart(chart_workload_balance(df), use_container_width=True, key="workload_balance_overview")
    with qa_tab:
        question=st.text_input("Your question",placeholder="What did we decide about the launch date?")
        if st.button("Ask MeetMind AI",use_container_width=True):
            with st.spinner("Searching meeting context..."):
                r=answer_meeting_question(question,st.session_state.transcript_text,st.session_state.meeting_summary,api_key)
            if r.success: st.session_state.qa_history.append((question,r.data.get('answer','')))
            else: st.warning(r.error)
        for q,a in reversed(st.session_state.qa_history[-5:]):
            with st.expander(f"Q: {q}",expanded=True): st.write(a)
    with email_tab:
        if st.button("Generate Follow-up Email",use_container_width=True):
            with st.spinner("Drafting follow-up email..."):
                records=df.drop(columns=['ID'],errors='ignore').to_dict(orient='records')
                r=generate_followup_email(st.session_state.meeting_title,st.session_state.meeting_summary,records,api_key)
            if r.success: st.session_state.followup_email=r.data.get('email','')
            else: st.warning(r.error)
        if st.session_state.followup_email:
            st.text_area("Generated Email",value=st.session_state.followup_email,height=300)
            st.download_button("Download Email",st.session_state.followup_email,"meetmind_followup_email.txt","text/plain")


if page == "📋  Action Items":
    # --------------------------------------------------------------------------
    # Action item workspace
    # --------------------------------------------------------------------------

    st.header("🔍 Search & Filter")

    f1, f2, f3, f4, f5 = st.columns([2, 1, 1, 1, 1])
    with f1:
        st.session_state.filter_search = st.text_input(
            "Search tasks", value=st.session_state.filter_search, placeholder="Search by keyword..."
        )
    with f2:
        owner_options = ["All"] + sorted(df["Owner"].dropna().astype(str).unique().tolist()) if not df.empty and "Owner" in df.columns else ["All"]
        st.session_state.filter_owner = st.selectbox(
            "Owner", owner_options,
            index=owner_options.index(st.session_state.filter_owner)
            if st.session_state.filter_owner in owner_options else 0,
        )
    with f3:
        priority_opts = ["All"] + PRIORITY_OPTIONS
        st.session_state.filter_priority = st.selectbox(
            "Priority", priority_opts, index=priority_opts.index(st.session_state.filter_priority)
        )
    with f4:
        status_opts = ["All"] + STATUS_OPTIONS
        st.session_state.filter_status = st.selectbox(
            "Status", status_opts, index=status_opts.index(st.session_state.filter_status)
        )
    with f5:
        st.session_state.filter_overdue_only = st.checkbox(
            "Overdue only", value=st.session_state.filter_overdue_only
        )

    filtered_df = apply_filters(
        df,
        search_text=st.session_state.filter_search,
        owner=st.session_state.filter_owner,
        priority=st.session_state.filter_priority,
        status=st.session_state.filter_status,
        overdue_only=st.session_state.filter_overdue_only,
    )


    # --------------------------------------------------------------------------
    # Section 5 — Interactive Data Editor
    # --------------------------------------------------------------------------

    hcol1, hcol2 = st.columns([5, 1])
    with hcol1:
        st.header("🗂️ Action Items")
    with hcol2:
        if st.button("🔄 Refresh", key="refresh_shared_tasks", use_container_width=True):
            refreshed = load_action_items_from_workspace(st.session_state.get("current_meeting_id"))
            if not refreshed.empty:
                st.session_state.action_items_df = refreshed
                st.success("Latest shared task updates loaded.")
            st.rerun()

    if filtered_df.empty:
        st.warning("No action items match the current filters." if not df.empty
                   else "No action items were extracted from this transcript.")
    else:
        edited_df = st.data_editor(
            filtered_df,
            column_config={
                "ID": st.column_config.NumberColumn("ID", disabled=True),
                "Task": st.column_config.TextColumn("Task", width="large"),
                "Owner": st.column_config.TextColumn("Owner"),
                "Deadline": st.column_config.TextColumn("Deadline"),
                "Priority": st.column_config.SelectboxColumn("Priority", options=PRIORITY_OPTIONS),
                "Status": st.column_config.SelectboxColumn("Status", options=STATUS_OPTIONS),
                "Dependency": st.column_config.TextColumn("Dependency"),
                "Context": st.column_config.TextColumn("Context", width="large"),
                "Confidence": st.column_config.ProgressColumn(
                    "AI Confidence", min_value=0.0, max_value=1.0, format="%.2f"
                ),
            },
            hide_index=True,
            use_container_width=True,
            num_rows="dynamic",
            key="data_editor_widget",
        )

        # Merge edits back into the full (unfiltered) session-state DataFrame
        if not edited_df.equals(filtered_df):
            full_df = st.session_state.action_items_df.set_index("ID")
            edited_indexed = edited_df.set_index("ID") if "ID" in edited_df.columns else edited_df
            for row_id, row in edited_indexed.iterrows():
                if row_id in full_df.index:
                    full_df.loc[row_id] = row
            # Handle newly added rows (num_rows="dynamic")
            new_rows = edited_indexed[~edited_indexed.index.isin(full_df.index)]
            if not new_rows.empty:
                full_df = pd.concat([full_df, new_rows])
            st.session_state.action_items_df = full_df.reset_index()
            try:
                save_action_items_to_workspace(st.session_state.get("current_meeting_id"), st.session_state.action_items_df)
                st.toast("Task updates saved for your shared workspace", icon="☁️")
            except Exception as exc:
                st.error(f"Could not save task updates: {exc}")
            st.rerun()


    # --------------------------------------------------------------------------
    # 6A — Action Tracker & Team Dashboard
    # --------------------------------------------------------------------------
    st.header("🎯 Action Tracker & Team Dashboard")
    tracker_tab, team_tab = st.tabs(["🎯 Action Tracker", "👥 Team Dashboard"])
    with tracker_tab:
        t1,t2,t3 = st.columns(3)
        t1.metric("Pending", int((df["Status"] == "Pending").sum()) if not df.empty else 0)
        t2.metric("In Progress", int((df["Status"] == "In Progress").sum()) if not df.empty else 0)
        t3.metric("Completed", int((df["Status"] == "Completed").sum()) if not df.empty else 0)
        if not df.empty:
            tracker = df[[c for c in ["Task","Owner","Deadline","Priority","Status","Dependency"] if c in df.columns]].copy()
            st.dataframe(tracker.sort_values(["Status","Priority"], kind="stable"), use_container_width=True, hide_index=True)
            st.caption("Tip: change task status in the editable Action Items table; this tracker updates immediately.")
    with team_tab:
        if not df.empty and "Owner" in df.columns:
            team = df.copy()
            workload = team.groupby("Owner", dropna=False).agg(Tasks=("Task","count"), Completed=("Status", lambda x: int((x=="Completed").sum())), High_Priority=("Priority", lambda x: int((x=="High").sum()))).reset_index()
            workload["Completion %"] = (workload["Completed"] / workload["Tasks"] * 100).round(1)
            st.dataframe(workload, use_container_width=True, hide_index=True)
            st.plotly_chart(chart_workload_balance(df), use_container_width=True, key="workload_balance_team")
        else:
            st.info("Team workload will appear after action items are extracted.")


if page == "🗂️  Meeting History":
    # --------------------------------------------------------------------------
    # 6B — Meeting Search & AI Chat with History
    # --------------------------------------------------------------------------
    st.header("🔎 Meeting Search & AI Chat with History")
    search_tab, history_chat_tab = st.tabs(["🔎 Search Meetings", "💬 Ask All Meetings"])
    with search_tab:
        q = st.text_input("Search titles, participants, summaries or transcripts", key="history_query")
        history = list(reversed(st.session_state.meeting_history))
        if q:
            ql=q.lower()
            history=[h for h in history if ql in (h.get("title","")+h.get("participants","")+h.get("summary","")+h.get("transcript","")).lower()]
        if history:
            for h in history:
                with st.expander(f"{h['title']} · {h['date']}"):
                    st.caption(f"Participants: {h.get('participants','Not recorded')}")
                    st.write(h.get('summary','No summary stored.'))
                    st.caption(f"{h.get('task_count',0)} tasks · {h.get('completion_pct',0)}% complete")
        else:
            st.caption("No matching meetings found. Shared workspace history is saved in the cloud workspace.")
    with history_chat_tab:
        all_q = st.text_input("Ask about every saved meeting", placeholder="What tasks were assigned to Bhoomi across all meetings?", key="all_history_question")
        if st.button("Ask Meeting History", use_container_width=True):
            if not st.session_state.meeting_history:
                st.warning("Process at least one meeting first. Additional meetings will be added to this session's history.")
            elif all_q.strip():
                context = "\n\n--- MEETING ---\n".join([f"Title: {h.get('title')}\nDate: {h.get('date')}\nParticipants: {h.get('participants','')}\nSummary: {h.get('summary','')}\nTranscript: {h.get('transcript','')}" for h in st.session_state.meeting_history])
                with st.spinner("Searching across your meeting history..."):
                    r=answer_meeting_question(all_q, context, "Answer using all saved meeting history and clearly mention meeting dates/titles when relevant.", api_key)
                if r.success: st.session_state.history_answer=r.data.get('answer','')
                else: st.warning(r.error)
        if st.session_state.history_answer:
            st.info(st.session_state.history_answer)


if page == "📋  Action Items":
    # --------------------------------------------------------------------------
    # Analytics
    # --------------------------------------------------------------------------

    st.header("📈 Analytics")

    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(chart_tasks_by_owner(filtered_df), use_container_width=True, key="tasks_by_owner_chart")
    with c2:
        st.plotly_chart(chart_tasks_by_priority(filtered_df), use_container_width=True, key="tasks_by_priority_chart")

    c3, c4 = st.columns(2)
    with c3:
        st.plotly_chart(chart_tasks_by_status(filtered_df), use_container_width=True, key="tasks_by_status_chart")
    with c4:
        st.plotly_chart(chart_deadline_distribution(filtered_df), use_container_width=True, key="deadline_distribution_chart")


    # --------------------------------------------------------------------------
    # Section 7 — Export
    # --------------------------------------------------------------------------

    st.header("⬇️ Export")

    e1, e2, e3, e4 = st.columns(4)

    current_df = st.session_state.action_items_df

    with e1:
        st.download_button(
            "Download CSV",
            data=to_csv_bytes(current_df),
            file_name=f"meetmind_action_items_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
            mime="text/csv",
            use_container_width=True,
        )
    with e2:
        st.download_button(
            "Download JSON",
            data=to_json_bytes(
                st.session_state.meeting_title, str(st.session_state.meeting_date),
                st.session_state.team_project, st.session_state.participants,
                st.session_state.meeting_summary, st.session_state.key_decisions,
                current_df, st.session_state.risks_and_blockers,
                st.session_state.follow_up_questions,
            ),
            file_name=f"meetmind_report_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json",
            use_container_width=True,
        )
    with e3:
        st.download_button(
            "Download Markdown Report",
            data=to_markdown_report(
                st.session_state.meeting_title, str(st.session_state.meeting_date),
                st.session_state.team_project, st.session_state.participants,
                st.session_state.meeting_summary, st.session_state.key_decisions,
                current_df, st.session_state.risks_and_blockers,
                st.session_state.follow_up_questions,
            ),
            file_name=f"meetmind_report_{datetime.now().strftime('%Y%m%d_%H%M')}.md",
            mime="text/markdown",
            use_container_width=True,
        )
    with e4:
        st.download_button("Download Text Report", data=to_text_report(st.session_state.meeting_title,str(st.session_state.meeting_date),st.session_state.team_project,st.session_state.participants,st.session_state.meeting_summary,st.session_state.key_decisions,current_df,st.session_state.risks_and_blockers,st.session_state.follow_up_questions), file_name=f"meetmind_report_{datetime.now().strftime('%Y%m%d_%H%M')}.txt", mime="text/plain", use_container_width=True)

    st.divider()
    st.caption("MeetMind AI · Built with Streamlit + Gemini · MirAI School of Technology Capstone Project")

