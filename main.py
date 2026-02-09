

import os
import json
import base64
from typing import TypedDict, List, Dict, Literal, Annotated
from datetime import datetime
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph.message import add_messages

from config import CONFIG


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


Decision = Literal["skip", "delete"]   # Only two choices


class AgentState(TypedDict):
    emails: List[Email]
    groups: Dict[str, SenderGroup]
    decisions: Dict[str, Decision]
    saved_paths: Annotated[List[str], add_messages]
    trashed_ids: Annotated[List[str], add_messages]
    max_fetch: int
    run_started: str


def get_gmail_service(config=CONFIG):
    creds = None
    if config.token_file.exists():
        creds = Credentials.from_authorized_user_file(str(config.token_file), config.scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not config.credentials_file.exists():
                raise FileNotFoundError(f"Missing credentials file: {config.credentials_file}")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(config.credentials_file), config.scopes
            )
            creds = flow.run_local_server(port=0)
        with config.token_file.open("w") as f:
            f.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)


SERVICE = get_gmail_service()


def fetch_emails(state: AgentState) -> AgentState:
    print(f"Fetching up to {state['max_fetch']} messages...")
    try:
        res = SERVICE.users().messages().list(
            userId="me",
            labelIds=["INBOX"],
            maxResults=state["max_fetch"],
        ).execute()

        ids = [m["id"] for m in res.get("messages", [])]
        emails: List[Email] = []

        for i, mid in enumerate(ids, 1):
            print(f"  {i}/{len(ids)}", end="\r")
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
                except Exception:
                    preview = "[decode error]"

            emails.append({
                "id": mid,
                "subject": headers.get("subject", "(no subject)"),
                "sender": headers.get("from", "Unknown"),
                "date": headers.get("date", "Unknown"),
                "body_b64": body_b64,
                "preview": preview,
            })

        print(f"\n→ Fetched {len(emails)} emails")
        return {"emails": emails}

    except HttpError as e:
        print(f"Fetch error: {e}")
        return {"emails": []}


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

    print(f"→ Grouped into {len(groups)} senders")
    return {"groups": groups}


def human_review(state: AgentState) -> AgentState:
    groups = state["groups"]
    decisions: Dict[str, Decision] = {}

    print("\n" + "═" * 80)
    print("  SENDER REVIEW  (senders with most emails first)")
    print("═" * 80 + "\n")

    for sender, group in sorted(groups.items(), key=lambda x: -x[1]["count"]):
        print(f" {group['count']:3d}  {group['sender']}")
        
        for e in group["emails"][:2]:
            subj = e["subject"][:60] + "…" if len(e["subject"]) > 60 else e["subject"]
            print(f"      • {subj}")
        
        if group["count"] > 2:
            print(f"      … +{group['count']-2} more")

        while True:
            prompt = f"\n→ {group['sender']}  [d = delete (save locally + trash), Enter = skip] : "
            choice = input(prompt).strip().lower()

            if not choice or choice in ("skip", "keep", "k"):
                decisions[sender] = "skip"
                print("   → skipped (kept in Gmail)")
                break
            
            if choice in ("d", "delete", "t", "trash", "remove"):
                decisions[sender] = "delete"
                print("   → will save locally then trash from Gmail")
                break
            
            print("   ?  Only two choices: d = delete (save+trash), Enter = skip")

        print()  # spacing

    return {"decisions": decisions}


def execute_actions(state: AgentState) -> AgentState:
    saved = []
    trashed = []

    print("\nExecuting decisions...\n")

    # Process only senders marked for deletion
    for sender, decision in state["decisions"].items():
        if decision != "delete":
            continue

        group = state["groups"].get(sender)
        if not group:
            continue

        safe_sender = sender.replace("@", "_").replace(".", "_")[:48]

        # Phase 1: SAVE LOCALLY (always first!)
        print(f"  Saving {group['count']} emails from {sender} ...")
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
            print(f"     saved → {filename}")

        # Phase 2: TRASH from Gmail
        print(f"  Trashing {group['count']} messages from {sender} ...")
        for email in group["emails"]:
            try:
                SERVICE.users().messages().trash(userId="me", id=email["id"]).execute()
                trashed.append(email["id"])
                print(f"     trashed → {email['id'][:8]}…")
            except HttpError as e:
                print(f"     failed to trash {email['id']}: {e}")

    return {
        "saved_paths": saved,
        "trashed_ids": trashed,
    }


workflow = StateGraph(state_schema=AgentState)

workflow.add_node("fetch", fetch_emails)
workflow.add_node("group", group_by_sender)
workflow.add_node("human_review", human_review)
workflow.add_node("execute", execute_actions)

workflow.set_entry_point("fetch")
workflow.add_edge("fetch", "group")
workflow.add_edge("group", "human_review")
workflow.add_edge("human_review", "execute")
workflow.add_edge("execute", END)

memory = MemorySaver()
app = workflow.compile(checkpointer=memory)

if __name__ == "__main__":
    print("Gmail Cleanup Agent (only skip or delete)\n")

    thread_id = f"{CONFIG.thread_prefix}{datetime.now():%Y%m%d-%H%M%S}"
    run_config = {"configurable": {"thread_id": thread_id}}

    initial_state = {
        "max_fetch": CONFIG.max_fetch,
        "run_started": datetime.now().isoformat(),
        "saved_paths": [],
        "trashed_ids": [],
    }

    try:
        final = app.invoke(initial_state, run_config)
        print("\n" + "═" * 80)
        print("  FINISHED")
        print(f"  Saved  : {len(final['saved_paths'])} files")
        print(f"  Trashed: {len(final['trashed_ids'])} messages")
        print("═" * 80)
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    except Exception as exc:
        print(f"Error: {exc}")