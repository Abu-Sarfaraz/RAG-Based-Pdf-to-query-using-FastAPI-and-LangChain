"""Streamlit UI for the RAG PDF QA system.

Calls the FastAPI backend for ingestion and question answering.
"""
from __future__ import annotations

import os
from typing import Any

import requests
import streamlit as st

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
COLLECTION = os.getenv("COLLECTION", "pdf_docs")


def _post(path: str, *, files=None, json=None, timeout: int = 300) -> tuple[dict | None, str | None]:
    try:
        r = requests.post(f"{BACKEND_URL}{path}", files=files, json=json, timeout=timeout)
        r.raise_for_status()
    except requests.HTTPError as exc:
        return None, exc.response.text if exc.response is not None else str(exc)
    except requests.RequestException as exc:
        return None, str(exc)
    return r.json(), None


st.set_page_config(page_title="RAG PDF QA", page_icon="📄", layout="centered")
st.title("📄 RAG PDF QA")
st.caption("Upload a PDF, then ask questions grounded in its content.")

st.header("1. Upload a PDF")
uploaded = st.file_uploader("Choose a PDF file", type=["pdf"])

if uploaded is not None and st.button("Ingest this PDF", type="primary"):
    with st.spinner("Loading, splitting, embedding, storing..."):
        files = {"file": (uploaded.name, uploaded.getvalue(), "application/pdf"), "collection": (None, COLLECTION)}
        result, err = _post("/ingest", files=files)
    if err is not None:
        st.error(f"Ingest failed: {err}")
    elif result is not None:
        st.session_state["last_ingest"] = result
        st.success(
            f"Ingested **{uploaded.name}** — "
            f"{result['pages']} pages, {result['chunks']} chunks "
            f"into collection `{result['collection']}`."
        )

st.header("2. Ask a question")
question = st.text_input("Your question", placeholder="What is this document about?")
k = st.slider("Top-k", min_value=1, max_value=20, value=4)

if st.button("Ask", type="primary", disabled=not question) and question:
    with st.spinner("Retrieving context and generating answer..."):
        payload: dict[str, Any] = {"question": question, "collection": COLLECTION, "k": int(k)}
        result, err = _post("/ask", json=payload)
    if err is not None:
        st.error(f"Ask failed: {err}")
    elif result is not None:
        st.subheader("Answer")
        st.write(result["answer"])

        sources = result.get("sources") or []
        if sources:
            with st.expander(f"Sources ({len(sources)})"):
                for i, s in enumerate(sources, start=1):
                    page = s.get("page")
                    page_str = f"page {page}" if page is not None else "page ?"
                    st.markdown(f"**[{i}] {page_str}**")
                    st.write(s.get("snippet", ""))