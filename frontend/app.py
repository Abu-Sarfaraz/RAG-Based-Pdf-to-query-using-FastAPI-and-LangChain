"""Papertrail: attach a PDF, ask once, and continue a saved conversation."""
from __future__ import annotations

import base64
import hashlib
import html
import os
from pathlib import Path
import re
import uuid

import streamlit as st
import streamlit.components.v1 as components

from api import API, APIError

ROOT = Path(__file__).resolve().parent
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
browser_workspace = components.declare_component("papertrail_workspace", path=str(ROOT / "components/workspace"))
pdf_viewer = components.declare_component("papertrail_pdf", path=str(ROOT / "components/pdf_viewer"))
copy_answer = components.declare_component("papertrail_copy", path=str(ROOT / "components/copy"))

st.set_page_config(page_title="Papertrail · Answers in your documents", page_icon="📑", layout="wide")
st.html("<style>" + (ROOT / "styles.css").read_text() + "</style>")


def activate(document_id, conversation_id=None):
    st.session_state.active_document = document_id
    st.session_state.active_conversation = conversation_id
    st.session_state.switch_document_to = document_id
    st.session_state.source_page = 1
    st.session_state.source_request = st.session_state.get("source_request", 0) + 1
    st.session_state.pop("last_page_query", None)
    st.session_state.selected_source = None
    st.session_state.composer_epoch = st.session_state.get("composer_epoch", 0) + 1
    if conversation_id:
        st.query_params["chat"] = conversation_id
    else:
        st.query_params.clear()


def choose_source(source):
    st.session_state.selected_source = source
    st.session_state.source_page = source["page"]
    st.session_state.source_request = st.session_state.get("source_request", 0) + 1
    st.session_state.open_pdf_panel = True
    st.session_state.mobile_pdf_expanded = True


def queue_question(question, document_id=None):
    st.session_state.pending_question = {
        "question": question, "document_id": document_id or st.session_state.active_document,
        "request_id": uuid.uuid4().hex, "k": st.session_state.get("retrieval_count", 4),
    }


@st.cache_data(ttl=120, max_entries=2, show_spinner=False)
def original_pdf(base_url, token, document_id):
    with API(base_url, token).request("GET", f"/documents/{document_id}/file") as response:
        return response.content


def upload(api, file):
    data = file.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    known = st.session_state.setdefault("uploaded_files", {})
    if digest not in known:
        with st.spinner("Saving your PDF…"):
            document = api.upload(file.name, data)
        known[digest] = document["id"]
    if known[digest] != st.session_state.active_document:
        activate(known[digest])
    return known[digest]


def linked_answer(answer, sources, conversation_id):
    source_map = {source["id"]: source for source in sources}
    def link(match):
        source = source_map.get(int(match[1]))
        if source is None:
            return match.group()
        return f"[{match.group()}](?chat={conversation_id}&page={source['page']}&citation={source['id']})"
    return re.sub(r"\[(\d+)\]", link, answer)


def initialize():
    defaults = {"token": None, "active_document": None, "active_conversation": None,
                "source_page": 1, "selected_source": None, "show_pdf": True,
                "pending_question": None, "uploaded_files": {}, "retrieval_count": 4,
                "composer_epoch": 0, "uploader_epoch": 0, "source_request": 0}
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)
    if st.session_state.pop("open_pdf_panel", False):
        st.session_state.show_pdf = True
        st.session_state.pdf_panel_toggle = True
    storage_key = "papertrail.workspace.v1." + hashlib.sha256(BACKEND_URL.encode()).hexdigest()[:12]
    browser = browser_workspace(storage_key=storage_key, token=st.session_state.token,
                                key="workspace-memory", default=None)
    if browser is None:
        st.caption("Opening your workspace…")
        st.stop()
    if not st.session_state.token:
        st.session_state.token = browser.get("token") or API(BACKEND_URL).json("POST", "/workspaces")["token"]
        st.rerun()
    st.session_state.mobile_view = browser.get("mobile", False)
    api = API(BACKEND_URL, st.session_state.token)
    overview = api.json("GET", "/workspace")
    documents = overview["documents"]
    conversations = overview["conversations"]
    requested_chat = st.query_params.get("chat")
    if requested_chat and requested_chat != st.session_state.active_conversation:
        chat = next((chat for chat in conversations if chat["id"] == requested_chat), None)
        if chat:
            activate(chat["document_id"], chat["id"])
    if not st.session_state.active_document and documents:
        latest_chat = next((chat for chat in conversations if chat["document_id"] == documents[0]["id"]), None)
        activate(documents[0]["id"], latest_chat["id"] if latest_chat else None)
    if st.session_state.active_document and not any(doc["id"] == st.session_state.active_document for doc in documents):
        activate(documents[0]["id"] if documents else None)
    if "switch_document_to" in st.session_state:
        selected = st.session_state.pop("switch_document_to")
        if selected:
            st.session_state.library_choice = selected
    return api, overview, documents, conversations


def sidebar(api, overview, documents, conversations):
    waiting = st.session_state.pending_question is not None
    with st.sidebar:
        st.markdown('<div class="brand"><span class="brand-icon">P</span><span>papertrail<span class="brand-dot">.</span></span></div>', unsafe_allow_html=True)
        st.caption("A little less reading. A lot more clarity.")
        st.markdown("<div class='section-label'>YOUR LIBRARY</div>", unsafe_allow_html=True)
        added = st.file_uploader("Add a PDF", type=["pdf"], key=f"sidebar-upload-{st.session_state.uploader_epoch}", disabled=waiting,
                                 help=f"Searchable PDFs, up to {overview['max_upload_mb']} MB.")
        if added is not None:
            digest = hashlib.sha256(added.getvalue()).hexdigest()
            if digest not in st.session_state.uploaded_files:
                upload(api, added)
                st.rerun()
        if documents:
            ids = [doc["id"] for doc in documents]
            by_id = {doc["id"]: doc for doc in documents}
            if st.session_state.get("library_choice") not in ids:
                st.session_state.library_choice = st.session_state.active_document or ids[0]
            def change_document():
                document_id = st.session_state.library_choice
                chat = next((chat for chat in conversations if chat["document_id"] == document_id), None)
                activate(document_id, chat["id"] if chat else None)
            st.selectbox("Your PDFs", ids, format_func=lambda value: by_id[value]["filename"],
                         key="library_choice", on_change=change_document, disabled=waiting)
            active = by_id.get(st.session_state.active_document)
            if active:
                st.caption(f"{len(documents)} saved PDF{'s' if len(documents) != 1 else ''} · " + (f"{active['pages']} pages" if active["pages"] else "Preparing"))
                if st.button("New conversation", icon=":material/add_comment:", use_container_width=True,
                             disabled=waiting or active["status"] != "ready"):
                    activate(active["id"])
                    st.rerun()
                with st.popover("Document options", use_container_width=True):
                    if st.button("Remove PDF and its conversations", disabled=waiting or active["status"] not in ("ready", "failed")):
                        api.json("DELETE", f"/documents/{active['id']}")
                        st.session_state.uploaded_files = {digest: value for digest, value in st.session_state.uploaded_files.items() if value != active["id"]}
                        st.session_state.uploader_epoch += 1
                        activate(None)
                        st.rerun()
        else:
            st.caption("Your PDFs will appear here.")
        st.markdown("<div class='section-label'>RECENT CONVERSATIONS</div>", unsafe_allow_html=True)
        if not conversations:
            st.caption("Your first question starts a conversation.")
        for chat in conversations[:8]:
            if st.button(chat["title"], key="chat-" + chat["id"], use_container_width=True,
                         type="primary" if chat["id"] == st.session_state.active_conversation else "secondary",
                         disabled=waiting, help=chat["filename"]):
                activate(chat["document_id"], chat["id"])
                st.rerun()
        st.divider()
        def toggle_pdf():
            st.session_state.show_pdf = st.session_state.pdf_panel_toggle
        st.toggle("PDF & sources", value=True, key="pdf_panel_toggle", on_change=toggle_pdf)
        with st.expander("Advanced settings"):
            st.slider("Passages to search", 1, 12, value=4, key="retrieval_count",
                      help="More passages may help broad questions, but make answers slower.")
        with st.popover("Workspace & recovery", use_container_width=True):
            st.caption("Your workspace is restored automatically in this browser. Save its recovery code to open it on another device.")
            if st.checkbox("Show my recovery code"):
                st.code(st.session_state.token, language=None)
                st.caption("Anyone with this code can open your workspace.")
            code = st.text_input("Restore another workspace", type="password", placeholder="Paste a recovery code")
            if st.button("Restore workspace", disabled=not code):
                API(BACKEND_URL, code.strip()).json("GET", "/workspace")
                st.session_state.token = code.strip()
                st.session_state.uploaded_files = {}
                st.session_state.uploader_epoch += 1
                activate(None)
                st.rerun()
        st.markdown('<div class="sidebar-footer">GROUND YOUR CURIOSITY<br><span>Answers with a paper trail.</span></div>', unsafe_allow_html=True)


@st.fragment(run_every="1s")
def preparation(api, document_id):
    try:
        document = api.json("GET", f"/documents/{document_id}")
    except APIError as error:
        st.error(str(error))
        if st.button("Reconnect", key="reconnect-" + document_id):
            st.rerun()
        return
    status = document["status"]
    if status in ("ready", "failed"):
        st.rerun()
    labels = {"uploading": "Saving your PDF", "queued": "Your PDF is next in line",
              "reading": "Reading your PDF", "preparing": "Preparing answers"}
    with st.status(labels.get(status, "Preparing your PDF"), expanded=True):
        st.write("You can send a question now. We’ll answer as soon as your PDF is ready.")
        if document["pages"]:
            st.caption(f"{document['pages']} pages read")


def message_sources(message):
    sources = message.get("sources") or []
    if not sources:
        return
    st.caption("PAGES CONSULTED · select a reference to open the PDF")
    for start in range(0, len(sources), 3):
        columns = st.columns(min(3, len(sources) - start))
        for column, source in zip(columns, sources[start:start + 3]):
            with column:
                st.button(f"[{source['id']}] Page {source['page_label']}",
                          key=f"source-{message['id']}-{source['id']}", use_container_width=True,
                          on_click=choose_source, args=(source,))


def render_history(messages, conversation_id):
    last_user = None
    for message in messages:
        if message["role"] == "user":
            last_user = message["content"]
        avatar = ":material/person:" if message["role"] == "user" else ":material/auto_stories:"
        with st.chat_message(message["role"], avatar=avatar):
            st.markdown(linked_answer(message["content"], message.get("sources", []), conversation_id))
            if message["role"] == "assistant":
                if message["status"] == "failed":
                    st.error(message.get("metrics", {}).get("error", "Answer interrupted. Please retry."))
                message_sources(message)
                actions = st.columns([1.5, 1, 3])
                with actions[0]:
                    copy_answer(text=message["content"], key=f"copy-{message['id']}", default=None)
                with actions[1]:
                    if st.button("Retry", key=f"retry-{message['id']}", disabled=not last_user):
                        st.session_state.pending_question = {
                            "question": last_user, "document_id": st.session_state.active_document,
                            "request_id": message["request_id"] if message["status"] == "failed" else uuid.uuid4().hex,
                            "k": st.session_state.retrieval_count,
                        }
                        st.rerun()


def sources_panel(api, document, messages):
    with st.container(border=True):
        st.markdown("<div class='section-label source-title'>THE PAPER TRAIL</div>", unsafe_allow_html=True)
        st.markdown(f"**{document['filename']}**")
        selected = st.session_state.selected_source
        if selected is None:
            available = [source for message in reversed(messages) for source in message.get("sources", [])]
            selected = available[0] if available else None
        page_query = st.query_params.get("page")
        if page_query and st.session_state.get("last_page_query") != page_query:
            try:
                page = int(page_query)
                st.session_state.source_page = max(1, min(document["pages"] or 1, page))
                st.session_state.source_request += 1
                st.session_state.last_page_query = page_query
                selected = next((source for message in reversed(messages) for source in message.get("sources", [])
                                 if source["page"] == page), selected)
                st.session_state.selected_source = selected
            except ValueError:
                pass
        data = original_pdf(BACKEND_URL, st.session_state.token, document["id"])
        value = pdf_viewer(data=base64.b64encode(data).decode(), document_id=document["id"],
                           page=st.session_state.source_page, request_id=st.session_state.source_request, height=500,
                           key="viewer-" + document["id"], default=None)
        seen_key = "viewer-state-" + document["id"]
        if value and value != st.session_state.get(seen_key):
            st.session_state.source_page = value["page"]
            st.session_state[seen_key] = value
        if selected:
            st.caption(f"REFERENCE [{selected['id']}] · PAGE {selected['page_label']}")
            st.write(selected["excerpt"])
        else:
            st.caption("Ask a question to see the passages behind your answer.")
        st.download_button("Download PDF", data=data, file_name=document["filename"], mime="application/pdf",
                           use_container_width=True, on_click="ignore", key="download-" + document["id"])


def send_pending(api, document, messages):
    pending = st.session_state.pending_question
    if not pending or document is None or document["id"] != pending["document_id"] or document["status"] != "ready":
        return
    conversation_id = st.session_state.active_conversation
    if not conversation_id:
        chat = api.json("POST", "/conversations", json={"document_id": document["id"]})
        conversation_id = chat["id"]
        st.session_state.active_conversation = conversation_id
        st.query_params["chat"] = conversation_id
    st.session_state.pending_question = None
    st.session_state.pop("stream_error", None)
    st.session_state.pop("retry_payload", None)
    with st.chat_message("user", avatar=":material/person:"):
        st.write(pending["question"])
    with st.chat_message("assistant", avatar=":material/auto_stories:"):
        state = {"error": None, "done": None}
        status = st.empty()
        status.caption("Finding relevant passages…")
        def tokens():
            for event in api.stream(conversation_id, {key: pending[key] for key in ("question", "k", "request_id")}):
                if event["event"] == "sources":
                    status.caption("Writing your answer…")
                elif event["event"] == "token":
                    status.empty()
                    yield event["text"]
                elif event["event"] == "done":
                    state["done"] = event
                    if event.get("sources"):
                        choose_source(event["sources"][0])
                elif event["event"] == "error":
                    state["error"] = event["message"]
        try:
            st.write_stream(tokens())
        except APIError as error:
            state["error"] = str(error)
        status.empty()
        if state["error"] or not state["done"]:
            st.session_state.stream_error = state["error"] or "The response was interrupted. Please retry."
            st.session_state.retry_payload = pending
    st.rerun()


def main():
    api, overview, documents, conversations = initialize()
    sidebar(api, overview, documents, conversations)
    document = next((doc for doc in documents if doc["id"] == st.session_state.active_document), None)
    messages = []
    if st.session_state.active_conversation:
        chat = api.json("GET", f"/conversations/{st.session_state.active_conversation}")
        messages = chat["messages"]
    active_status = document["status"] if document else "empty"
    filename = html.escape(document["filename"] if document else "Your next great question")
    subtitle = f"{document['pages']} pages · Saved in your workspace" if document and document["pages"] else "A private workspace for your PDFs"
    st.markdown(f'<div class="workspace-header"><div><div class="eyebrow">DOCUMENT CHAT</div><h2>{filename}</h2><p>{subtitle}</p></div><span class="ready-badge">{"Ready" if active_status == "ready" else "Preparing" if document else "Welcome"}</span></div>', unsafe_allow_html=True)
    if not overview["answer_service_ready"]:
        st.info("The answer service needs to be connected before questions can be answered. You can still save and preview PDFs.")
    if st.session_state.get("stream_error"):
        st.error(st.session_state.stream_error)
        if st.button("Retry my last question"):
            st.session_state.pending_question = st.session_state.pop("retry_payload")
            st.session_state.pop("stream_error", None)
            st.rerun()
    use_panel = document is not None and st.session_state.show_pdf
    layout = st.columns([3, 2], gap="large") if use_panel else [st.container()]
    with layout[0]:
        if document and document["status"] not in ("ready", "failed"):
            preparation(api, document["id"])
        if document and document["status"] == "failed":
            st.error(document["error"])
            st.session_state.pending_question = None
            if st.button("Try preparing this PDF again"):
                api.json("POST", f"/documents/{document['id']}/retry")
                st.rerun()
        if not messages:
            st.markdown('<div class="empty-state"><div class="empty-icon">✦</div><div class="eyebrow">YOUR DOCUMENTS, UNDERSTOOD</div><h1>Read less.<br><span>Understand more.</span></h1><p>Find the answer in your PDF.<br>Keep the page that proves it.</p></div>', unsafe_allow_html=True)
            if document and document["status"] == "ready":
                st.caption("A GOOD PLACE TO START")
                for number, suggestion in enumerate(document["suggestions"]):
                    if st.button(suggestion, key=f"suggestion-{number}", use_container_width=True,
                                 disabled=not overview["answer_service_ready"]):
                        queue_question(suggestion)
                        st.rerun()
            else:
                st.caption("Attach a PDF below and send your first question. We’ll handle the preparation.")
        else:
            render_history(messages, st.session_state.active_conversation)
            transcript = "\n\n".join(f"{message['role'].upper()}\n{message['content']}" for message in messages)
            st.download_button("Export conversation", transcript, file_name="papertrail-conversation.md",
                               mime="text/markdown", on_click="ignore")
        if st.session_state.pending_question:
            pending = st.session_state.pending_question
            if document and document["status"] != "ready":
                st.info(f"Your question is saved: {pending['question']}")
            send_pending(api, document, messages)
    if use_panel:
        with layout[1]:
            if st.session_state.mobile_view:
                with st.expander("PDF and cited passages", expanded=st.session_state.get("mobile_pdf_expanded", False)):
                    sources_panel(api, document, messages)
            else:
                sources_panel(api, document, messages)
    prompt = st.chat_input("Attach a PDF and ask a question…" if not document else "Ask a question about this PDF…",
                           accept_file=True, file_type=["pdf"], key=f"composer-{st.session_state.composer_epoch}",
                           disabled=st.session_state.pending_question is not None or not overview["answer_service_ready"],
                           max_chars=4000)
    if prompt:
        target = document["id"] if document else None
        if prompt.files:
            target = upload(api, prompt.files[0])
        if prompt.text.strip():
            if not target:
                st.warning("Attach a PDF so we can find a grounded answer.")
                st.stop()
            queue_question(prompt.text.strip(), target)
        st.rerun()


try:
    main()
except APIError as error:
    st.error(str(error))
    if st.button("Reconnect"):
        st.rerun()
    if error.status == 401:
        recovery = st.text_input("Workspace recovery code", type="password")
        if st.button("Restore", disabled=not recovery):
            st.session_state.token = recovery.strip()
            st.session_state.uploaded_files = {}
            st.session_state.uploader_epoch += 1
            activate(None)
            st.rerun()
        if st.button("Start a new workspace"):
            st.session_state.token = API(BACKEND_URL).json("POST", "/workspaces")["token"]
            st.session_state.uploaded_files = {}
            st.session_state.uploader_epoch += 1
            activate(None)
            st.rerun()
