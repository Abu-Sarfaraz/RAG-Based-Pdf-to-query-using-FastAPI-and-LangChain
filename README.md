# RAG PDF QA

Upload a PDF, ask natural-language questions, get answers grounded in the PDF.

## Stack
- **FastAPI** backend (`/ingest`, `/ask`, `/health`)
- **Streamlit** frontend
- **LangChain** for loading → splitting → embedding → retrieval → LCEL chain
- **FAISS** for in-memory vector storage, persisted to disk between requests
- **Groq** (`llama-3.3-70b-versatile`) for chat
- **sentence-transformers** (`all-MiniLM-L6-v2`, 384 dims) for embeddings — runs locally

## Layout
```
rag-pdf-qa/
├── backend/
│   ├── app/
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── ingestion.py
│   │   ├── rag_chain.py
│   │   └── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── app.py
│   ├── requirements.txt
│   └── Dockerfile
├── docker-compose.yml
├── .env.example
└── README.md
```

## Quick start (local, no Docker)

### 1. Prereqs
- An API key for **Groq** (https://console.groq.com/keys). Embeddings are
  generated locally by `sentence-transformers` (`all-MiniLM-L6-v2`, 384 dims)
  — no API key needed; the model is bundled into the Docker image.

### 2. Configure env
```bash
cp .env.example .env
# fill in CHAT_API_KEY
```

### 3. Backend
```bash
cd backend
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

### 4. Frontend
```bash
cd frontend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Open http://localhost:8501, upload a PDF, ask a question.

## Quick start (Docker)
```bash
cp .env.example .env   # fill in secrets
docker compose up --build
```

## Endpoints

### `GET /health`
Returns `{ "status": "ok" }`.

### `POST /ingest`
Multipart form upload:
- file: PDF file
- collection (optional, default `pdf_docs`): namespace in the vector store

Response: `{ "pages": <int>, "chunks": <int>, "collection": "..." }`

### `POST /ask`
JSON body:
```json
{
  "question": "What is the document about?",
  "collection": "pdf_docs",
  "k": 4
}
```
Response:
```json
{
  "answer": "...",
  "sources": [
    { "page": 1, "snippet": "..." }
  ]
}
```

## Deploy to Render
1. Push to GitHub.
2. Create a Neon project, copy the connection string. Run `CREATE EXTENSION IF NOT EXISTS vector;` once in the Neon SQL console.
3. Create two Render web services from the same repo:
   - **backend** (Type: Web Service)
     - Root directory: `backend`
     - Build command: `pip install --upgrade pip && pip install -r requirements.txt`
     - Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
     - Instance type: **Starter ($7/mo, 1 GB RAM)** — free tier (512 MB) is too tight for the embedding model
     - Env vars: `NEON_DATABASE_URL`, `CHAT_API_KEY`, `LLM_MODEL` (no `CHAT_BASE_URL` — `ChatGroq` knows it)
   - **frontend** (Type: Web Service)
     - Root directory: `frontend`
     - Build command: `pip install --upgrade pip && pip install -r requirements.txt`
     - Start command: `streamlit run app.py --server.port $PORT --server.address 0.0.0.0`
     - Instance type: Free or Starter (no model loaded here)
     - Env vars: `BACKEND_URL` = the public URL of your backend service (e.g. `https://rag-backend-xxxx.onrender.com`)
4. Open the frontend's public URL.

> **Cold start on free tier**: Render's free tier spins down the container
> after 15 min of no traffic. The FAISS index lives in the container's
> filesystem, so a cold start wipes it. **You'll need to re-ingest your PDF
> after every cold start** (usually ~30-60s after the first idle). To make
> the index persistent, attach a Render Disk (paid plan feature).