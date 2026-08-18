# Planning

This document is a working notebook for ideas and topics you plan to show students about the Rag Pdf Qa project. Add entries in any order. Each entry should be a short note you can expand later into a script segment.

## Entry Format

For each entry:

- Title
- Why it matters for students
- What to cover (3 to 5 bullets)
- Status: draft / outlined / recorded

---

## Entry 1 - Scripting the backend

Status: draft

Title: Scripting -> creating backend all files by writing script

Why it matters for students:
- Many beginners install libraries but never see how a project is assembled from scratch.
- Watching the project get written file by file shows the full development flow, not just the final code.

What to cover:

- Open an empty folder and recreate the backend structure: backend/app/__init__.py, config.py, embeddings.py, db.py, ingestion.py, rag_chain.py, main.py
- Write requirements.txt and Dockerfile last, only after the application code works
- Explain each file as you write it, using the 3-beat rhythm from CONTENT_RECORDING_STRATEGY.md
- Show the imports building on each other so students understand why main.py is written last
- Highlight that the order is deliberate: config first, embeddings second, db third, ingestion fourth, rag_chain fifth, main last

---

## Entry 2 - Reranker concept

Status: draft

Title: Showing the concept of reranker

Why it matters for students:
- Students often assume retrieval returns perfect results, but vector similarity alone can miss relevant chunks.
- A reranker is a simple second pass that significantly improves answer quality without changing the rest of the pipeline.

What to cover:

- Problem: FAISS retrieval uses embedding similarity only, which can rank a loosely related chunk above a highly relevant one
- Solution: after retrieving the top k chunks, pass the question and each chunk through a cross-encoder reranker that scores actual question-document relevance
- The reranker returns a refined ranking; you keep the top n chunks (with n smaller than k) and feed only those into the LLM context
- Show how this improves answer grounding and reduces hallucinations
- Mention common options: sentence-transformers cross-encoders, Cohere rerank, BGE reranker
- Note the tradeoff: reranking adds latency and compute, so you retrieve more than you need and rerank down