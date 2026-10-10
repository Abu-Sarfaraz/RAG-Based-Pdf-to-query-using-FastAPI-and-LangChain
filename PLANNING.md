# Papertrail implementation

The first complete upgrade keeps Streamlit, FastAPI and FAISS. The user-facing flow is attachment + question → automatic preparation → streamed answer → cited PDF page → follow-up chat.

Implemented: browser-restored private workspaces; a persistent document/chat catalog; document isolation and deduplication; an ingestion queue with restart recovery; shared embedding model and bounded index cache; one retrieval per answer; conversation-aware follow-ups; real streaming; source IDs/page labels; PDF.js page navigation; question suggestions; copy, retry and export; upload validation; persistent Docker/Render configuration; a one-command local launcher; regression and live quality checks.

The supported deployment is one backend process per persistent data directory. A process lock prevents accidental competing workers. User files and index metadata are never committed. Index versions are published atomically, and docstores use JSON rather than pickle.

Extensions should follow observed needs: OCR for a scanned-document workload; hybrid search/reranking after evaluation shows retrieval misses; shared database/object/vector storage for multiple instances; React for advanced PDF annotation interactions. These are architecture choices rather than prerequisites for the current experience.
