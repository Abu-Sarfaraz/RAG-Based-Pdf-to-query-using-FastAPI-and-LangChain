# Content Recording Strategy

This document defines the correct order for recording the Rag Pdf Qa project, from the first file to the last, so the project is built completely and logically.

## Goal

Record the project step by step, file by file, in a build-along style where viewers code along with you. This is intended for beginners following the CampusX LangChain playlist.

## Pre-Recording Setup

Before recording:

1. Sanitize the .env file by replacing the real CHAT_API_KEY with a placeholder such as gsk_DEMO_KEY_REPLACE_ME. The real key must not appear on screen. Restore the real key after recording.
2. Pick a clean terminal with a dark theme, font size 16 to 18, and a wide window so code fits.
3. Show the folder tree once at the start so viewers see the layout before any code is written.
4. Open these tabs ready to record:
   - README.md and CLAUDE.md as narration reference
   - VS Code with backend/app and frontend/app.py
   - Browser at localhost:8501 for the final demo

## Recommended Recording Order

Use this exact sequence. The order matters because each file depends on the ones before it.

### Part 1 - Project Skeleton

1. Create the folder structure:

   ```
   rag-pdf-qa/
   └── backend/
       └── app/
   ```

2. Create the empty package file:

   ```
   backend/app/__init__.py
   ```

   Mention it briefly and move on. It is normally empty.

### Part 2 - Backend Foundation

3. `backend/app/config.py`

   Why first: every other backend file needs environment variables such as CHAT_API_KEY, LLM_MODEL, and FAISS_INDEX_DIR. Explain the Settings dataclass and env-var loading here.

4. `backend/app/embeddings.py`

   Loads the local sentence-transformers model all-MiniLM-L6-v2. Explain that it converts text into vectors and is used both during ingestion and during retrieval.

5. `backend/app/db.py`

   Implements the FAISS vector-store operations:

   - Check whether a collection exists
   - Create a FAISS index
   - Add documents to a collection
   - Save the index locally
   - Load an existing index
   - Return a retriever

   This is the first major backend module. Spend the most time here.

6. `backend/app/ingestion.py`

   Implements the PDF ingestion pipeline in this order:

   1. Load the PDF
   2. Split it into chunks
   3. Create embeddings
   4. Save the documents and vectors in FAISS
   5. Return the number of pages and chunks

   Data flow:

   ```
   PDF -> pages -> chunks -> embeddings -> FAISS
   ```

7. `backend/app/rag_chain.py`

   Implements the RAG chain:

   1. Load the selected FAISS collection
   2. Retrieve relevant document chunks
   3. Format chunks as context
   4. Build the prompt
   5. Send the prompt to Groq
   6. Parse the model response
   7. Return the answer and source information

   Important flow:

   ```
   question -> retrieve documents -> build prompt -> Groq -> answer
   ```

8. `backend/app/main.py`

   The FastAPI application. Implement endpoints in this order:

   - `GET /health` - liveness check
   - `POST /ingest` - accept the PDF, call ingestion, return pages, chunks, collection
   - `POST /ask` - accept the question, call the RAG function, return answer and sources

   This connects every previously created backend module.

### Part 3 - Backend Packaging

Record these after the backend code is working so viewers understand why each dependency is needed.

9. `backend/requirements.txt`
10. `backend/Dockerfile`

### Part 4 - Frontend

11. Create the frontend folder:

    ```
    frontend/
    ```

12. `frontend/requirements.txt`
13. `frontend/Dockerfile`

14. `frontend/app.py`

    Build the Streamlit UI in this order:

    1. Import Streamlit and requests
    2. Define BACKEND_URL
    3. Page title and description
    4. PDF file uploader
    5. Text field for the collection name
    6. Upload PDF button - calls `POST /ingest`
    7. Text field for the question
    8. Ask Question button - calls `POST /ask`
    9. Display the answer
    10. Display the retrieved sources

### Part 5 - Root Project Files

Record these last so you document and configure the project only after backend and frontend are both implemented.

15. `docker-compose.yml`
16. `render.yaml`
17. `.env.example`
18. `README.md`

## Per-File Recording Rhythm

For every source file, follow the same 3-beat rhythm:

1. Why does this file exist - one or two sentences
2. Read it top to bottom, explaining as you go
3. Highlight the 1 to 2 lines that matter most

Type every file yourself rather than pasting. Beginners benefit from seeing fingers on keyboard, even when it is slow.

## End-to-End Demo

After all files are recorded, do a full demo:

1. Boot the backend: `uvicorn app.main:app --reload --port 8000`
2. Boot the frontend: `BACKEND_URL=http://localhost:8000 streamlit run app.py`
3. Open browser at `localhost:8501`
4. Upload `demo.pdf`, click ingest, ask 2 to 3 questions, show sources

## Editing Checklist

After recording:

- Cut dead air, pauses, filler words
- Add zoom-ins on the key 1 to 2 lines per file (CapCut or DaVinci Resolve)
- Add chapter timestamps for YouTube
- Burn a subtle lower-third with the filename when each file appears
- Re-record voice-over during edits where you read code aloud

## Important Rule

Do not record `backend/app/main.py` first. It imports the other backend files, so viewers will not understand it without first seeing `config.py`, `embeddings.py`, `db.py`, `ingestion.py`, and `rag_chain.py`.