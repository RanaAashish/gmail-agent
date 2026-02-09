# app.py   →   streamlit run app.py

import streamlit as st
import json
import base64
from datetime import datetime
from pathlib import Path
from typing import TypedDict, List, Dict, Literal, Annotated

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import CONFIG

# ──────────────────────────────────────────────────────────────
# Session state initialization
# ──────────────────────────────────────────────────────────────

if "stage" not in st.session_state:
    st.session_state.stage = "welcome"          # welcome → fetching → review → executing → finished

if "state" not in st.session_state:
    st.session_state.state = None

if "thread_id" not in st.session_state:
    st.session_state.thread_id = f"{CONFIG.thread_prefix}{datetime.now():%Y%m%d-%H%M%S}"

if "decisions" not in st.session_state:
    st.session_state.decisions = {}

if "error" not in st.session_state:
    st.session_state.error = None

# ──────────────────────────────────────────────────────────────
# Types (copied from your code)
# ──────────────────────────────────────────────────────────────

class Email(TypedDict):
    id: str
    subject: str
    sender: str
    date: str
    body_b64: str
    preview: str

class SenderGroup(TypedDict):
    sender: str
    emails: List[Email]
    count: int

Decision = Literal["skip", "delete"]

class AgentState(TypedDict):
    emails: List[Email]
    groups: Dict[str, SenderGroup]
    decisions: Dict[str, Decision]
    saved_paths: List[str]
    trashed_ids: List[str]
    max_fetch: int
    run_started: str

# ──────────────────────────────────────────────────────────────
# Gmail Service (cached)
# ──────────────────────────────────────────────────────────────

@st.cache_resource
def get_gmail_service():
    creds = None
    token_path = CONFIG.token_file
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), CONFIG.scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CONFIG.credentials_file.exists():
                st.error(f"Missing credentials file: {CONFIG.credentials_file}")
                st.stop()
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CONFIG.credentials_file), CONFIG.scopes
            )
            creds = flow.run_local_server(port=0)
            with token_path.open("w") as f:
                f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)

SERVICE = get_gmail_service()

# ──────────────────────────────────────────────────────────────
# Core functions (slightly adapted)
# ──────────────────────────────────────────────────────────────

def fetch_emails(state: AgentState) -> AgentState:
    st.session_state.state = state
    st.info(f"Fetching up to {state['max_fetch']} messages...")

    try:
        res = SERVICE.users().messages().list(
            userId="me",
            labelIds=["INBOX"],
            maxResults=state["max_fetch"],
        ).execute()

        ids = [m["id"] for m in res.get("messages", [])]
        emails: List[Email] = []

        progress = st.progress(0)
        status_text = st.empty()

        for i, mid in enumerate(ids, 1):
            status_text.text(f"Fetching email {i}/{len(ids)}")
            progress.progress(i / len(ids))

            msg = SERVICE.users().messages().get(
                userId="me", id=mid, format="full"
            ).execute()

            headers = {h["name"].lower(): h["value"] for h in msg["payload"]["headers"]}

            body_b64 = ""
            payload = msg["payload"]
            if "parts" in payload:
                for part in payload["parts"]:
                    if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
                        body_b64 = part["body"]["data"]
                        break
            elif "body" in payload and "data" in payload["body"]:
                body_b64 = payload["body"]["data"]

            preview = ""
            if body_b64:
                try:
                    decoded = base64.urlsafe_b64decode(body_b64).decode("utf-8", errors="replace")
                    preview = decoded[:140] + "…" if len(decoded) > 140 else decoded
                except:
                    preview = "[decode error]"

            emails.append({
                "id": mid,
                "subject": headers.get("subject", "(no subject)"),
                "sender": headers.get("from", "Unknown"),
                "date": headers.get("date", "Unknown"),
                "body_b64": body_b64,
                "preview": preview,
            })

        status_text.success(f"Fetched {len(emails)} emails")
        progress.empty()

        return {**state, "emails": emails}

    except HttpError as e:
        st.error(f"Fetch error: {e}")
        return state

def group_by_sender(state: AgentState) -> AgentState:
    groups: Dict[str, SenderGroup] = {}

    for email in state.get("emails", []):
        sender_raw = email["sender"].strip()
        sender = (
            sender_raw.split("<")[1].split(">")[0].strip().lower()
            if "<" in sender_raw and ">" in sender_raw else sender_raw.lower()
        )

        if sender not in groups:
            groups[sender] = {"sender": sender, "emails": [], "count": 0}
        groups[sender]["emails"].append(email)
        groups[sender]["count"] += 1

    st.success(f"Grouped into {len(groups)} senders")
    return {**state, "groups": groups}

def execute_actions(state: AgentState) -> AgentState:
    saved = []
    trashed = []

    st.subheader("Executing cleanup...")

    for sender, decision in state["decisions"].items():
        if decision != "delete":
            continue

        group = state["groups"].get(sender)
        if not group:
            continue

        safe_sender = sender.replace("@", "_").replace(".", "_")[:48]

        # Save phase
        with st.status(f"Saving {group['count']} emails from {sender} ...", expanded=True):
            for email in group["emails"]:
                filename = f"{email['id']}_{safe_sender}.json"
                path = CONFIG.save_dir / filename

                payload = {
                    **email,
                    "archived_at": datetime.utcnow().isoformat(),
                    "decision": "delete",
                    "sender_normalized": sender,
                }

                with path.open("w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, ensure_ascii=False)

                saved.append(str(path))
                st.write(f"Saved → {filename}")

        # Trash phase
        with st.status(f"Trashing {group['count']} messages from {sender} ...", expanded=True):
            for email in group["emails"]:
                try:
                    SERVICE.users().messages().trash(userId="me", id=email["id"]).execute()
                    trashed.append(email["id"])
                    st.write(f"Trashed → {email['id'][:8]}…")
                except HttpError as e:
                    st.error(f"Failed to trash {email['id']}: {e}")

    return {**state, "saved_paths": saved, "trashed_ids": trashed}

# ──────────────────────────────────────────────────────────────
# Streamlit UI
# ──────────────────────────────────────────────────────────────

st.title("Gmail Cleanup Tool")
st.caption("Fetch → Review → Save locally + Trash selected senders")

# ─── Stage: Welcome ──────────────────────────────────────────────
if st.session_state.stage == "welcome":
    st.markdown("""
    This tool helps you clean your Gmail inbox by:
    - Fetching recent emails from INBOX
    - Grouping them by sender
    - Letting you decide per sender whether to **keep** or **delete** (save JSON locally + move to trash)
    """)

    col1, col2 = st.columns([3, 1])
    with col1:
        max_fetch = st.number_input(
            "How many recent emails to fetch?",
            min_value=10, max_value=500, value=CONFIG.max_fetch, step=10
        )

    if st.button("Start Cleanup", type="primary", use_container_width=True):
        st.session_state.state = {
            "max_fetch": max_fetch,
            "run_started": datetime.now().isoformat(),
            "saved_paths": [],
            "trashed_ids": [],
        }
        st.session_state.stage = "fetching"
        st.rerun()

# ─── Stage: Fetching & Grouping ──────────────────────────────────
elif st.session_state.stage == "fetching":
    st.session_state.state = fetch_emails(st.session_state.state)
    st.session_state.state = group_by_sender(st.session_state.state)
    st.session_state.stage = "review"
    st.rerun()

# ─── Stage: Review ───────────────────────────────────────────────
elif st.session_state.stage == "review":
    if "groups" not in st.session_state.state:
        st.error("No groups found. Something went wrong.")
        st.stop()

    groups = st.session_state.state["groups"]
    decisions = st.session_state.decisions

    st.subheader("Review & Decide")
    st.markdown("For each sender, choose whether to **keep** or **delete** (save locally + trash).")

    sorted_groups = sorted(groups.items(), key=lambda x: -x[1]["count"])

    for sender, group in sorted_groups:
        with st.expander(f"**{group['count']}** emails • {sender}", expanded=False):
            st.markdown("**First few subjects:**")
            for e in group["emails"][:3]:
                subj = e["subject"][:90] + "…" if len(e["subject"]) > 90 else e["subject"]
                st.write(f"• {subj}")

            if group["count"] > 3:
                st.caption(f"… +{group['count']-3} more")

            col1, col2 = st.columns(2)
            with col1:
                if st.button("Keep (skip)", key=f"keep_{sender}", use_container_width=True):
                    decisions[sender] = "skip"
                    st.rerun()
            with col2:
                if st.button("Delete (save + trash)", key=f"del_{sender}", type="primary", use_container_width=True):
                    decisions[sender] = "delete"
                    st.rerun()

            current = decisions.get(sender, "skip")
            st.info(f"**Current choice:** {'Delete' if current == 'delete' else 'Keep'}", icon="ℹ️")

    if st.button("Confirm choices & Run cleanup", type="primary", use_container_width=True):
        if not any(d == "delete" for d in decisions.values()):
            st.warning("No senders selected for deletion.")
        else:
            total_delete = sum(g["count"] for s, g in groups.items() if decisions.get(s) == "delete")
            st.session_state.state["decisions"] = decisions.copy()
            st.session_state.stage = "executing"
            st.rerun()

# ─── Stage: Executing ────────────────────────────────────────────
elif st.session_state.stage == "executing":
    st.session_state.state = execute_actions(st.session_state.state)
    st.session_state.stage = "finished"
    st.rerun()

# ─── Stage: Finished ─────────────────────────────────────────────
elif st.session_state.stage == "finished":
    st.success("Cleanup finished!")

    c1, c2 = st.columns(2)
    c1.metric("Saved files", len(st.session_state.state.get("saved_paths", [])))
    c2.metric("Trashed messages", len(st.session_state.state.get("trashed_ids", [])))

    st.markdown("**Saved files are stored in:**")
    st.code(str(CONFIG.save_dir.resolve()), language="text")

    if st.button("Start new session", type="primary"):
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

# Debug / current state
with st.sidebar.expander("Debug info"):
    st.json({
        "stage": st.session_state.stage,
        "thread_id": st.session_state.thread_id,
        "decisions_count": len(st.session_state.decisions),
        "state_keys": list(st.session_state.state.keys()) if st.session_state.state else []
    })