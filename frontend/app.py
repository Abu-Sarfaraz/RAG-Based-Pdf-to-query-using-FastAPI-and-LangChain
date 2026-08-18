"""Streamlit UI for the RAG PDF QA system.

Calls the FastAPI backend for ingestion and question answering.
"""  # browser UI; all heavy work stays on the API
from __future__ import annotations  # so `dict | None` works without extra imports

import os  # BACKEND_URL / COLLECTION overrides
from typing import Any  # JSON payload type hint

import requests  # HTTP client for /ingest and /ask
import streamlit as st  # the UI framework

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")  # no trailing slash before paths
COLLECTION = os.getenv("COLLECTION", "pdf_docs")  # must match the backend default


def _post(path: str, *, files=None, json=None, timeout: int = 300) -> tuple[dict | None, str | None]:  # one helper for both endpoints
    try:  # network and HTTP errors become UI messages
        r = requests.post(f"{BACKEND_URL}{path}", files=files, json=json, timeout=timeout)  # ingest is slow (embed)
        r.raise_for_status()  # 4xx/5xx → HTTPError
    except requests.HTTPError as exc:  # backend sent an error body
        return None, exc.response.text if exc.response is not None else str(exc)  # show FastAPI's message
    except requests.RequestException as exc:  # timeout, connection refused, etc.
        return None, str(exc)  # show why the call failed
    return r.json(), None  # success: parsed JSON, no error string


st.set_page_config(page_title="RAG PDF QA", page_icon="📄", layout="centered")  # tab title + compact layout
st.title("📄 RAG PDF QA")  # main heading
st.caption("Upload a PDF, then ask questions grounded in its content.")  # one-line how-to

st.header("1. Upload a PDF")  # step 1 of the flow
uploaded = st.file_uploader("Choose a PDF file", type=["pdf"])  # only accept PDFs

if uploaded is not None and st.button("Ingest this PDF", type="primary"):  # don't ingest until the user clicks
    with st.spinner("Loading, splitting, embedding, storing..."):  # ingest can take tens of seconds
        files = {"file": (uploaded.name, uploaded.getvalue(), "application/pdf"), "collection": (None, COLLECTION)}  # multipart like curl -F
        result, err = _post("/ingest", files=files)  # POST to FastAPI
    if err is not None:  # backend or network failed
        st.error(f"Ingest failed: {err}")  # red error box
    elif result is not None:  # ingest returned stats
        st.session_state["last_ingest"] = result  # remember last successful ingest
        st.success(  # green confirmation
            f"Ingested **{uploaded.name}** — "  # which file
            f"{result['pages']} pages, {result['chunks']} chunks "  # how much was indexed
            f"into collection `{result['collection']}`."  # which FAISS namespace
        )

st.header("2. Ask a question")  # step 2 of the flow
question = st.text_input("Your question", placeholder="What is this document about?")  # free-text query
k = st.slider("Top-k", min_value=1, max_value=20, value=4)  # how many chunks to retrieve

if st.button("Ask", type="primary", disabled=not question) and question:  # no empty submits
    with st.spinner("Retrieving context and generating answer..."):  # Groq + retrieval latency
        payload: dict[str, Any] = {"question": question, "collection": COLLECTION, "k": int(k)}  # AskRequest JSON
        result, err = _post("/ask", json=payload)  # POST JSON to FastAPI
    if err is not None:  # e.g. collection not found
        st.error(f"Ask failed: {err}")  # red error box
    elif result is not None:  # got answer + sources
        st.subheader("Answer")  # label the model output
        st.write(result["answer"])  # the generated text

        sources = result.get("sources") or []  # list of {page, snippet}
        if sources:  # only show the expander when we have citations
            with st.expander(f"Sources ({len(sources)})"):  # collapsed by default
                for i, s in enumerate(sources, start=1):  # numbered like the prompt
                    page = s.get("page")  # PDF page from PyPDFLoader metadata
                    page_str = f"page {page}" if page is not None else "page ?"  # missing metadata fallback
                    st.markdown(f"**[{i}] {page_str}**")  # bold citation header
                    st.write(s.get("snippet", ""))  # first 200 chars of the chunk