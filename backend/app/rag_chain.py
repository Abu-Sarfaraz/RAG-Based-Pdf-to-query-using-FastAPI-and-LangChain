"""Build the LCEL RAG chain: retriever -> prompt -> LLM -> parser."""  # question in, grounded answer out
from langchain_core.output_parsers import StrOutputParser  # LLM message → plain string
from langchain_core.prompts import ChatPromptTemplate  # system + human messages with placeholders
from langchain_core.runnables import RunnablePassthrough  # pass the question through unchanged
from langchain_groq import ChatGroq  # chat model hosted by Groq

from .config import settings  # API key and model name
from .db import get_retriever  # FAISS similarity search


SYSTEM_PROMPT = (  # instruct the model to stay inside retrieved context
    "You are a precise assistant. Answer the user's question using ONLY "  # no outside knowledge
    "the context provided below. If the answer isn't in the context, say "  # avoid hallucination
    "you don't know. Be concise.\n\n"  # short answers in the UI
    "Context:\n{context}"  # {context} filled by formatted retrieved chunks
)

_llm = ChatGroq(  # one client reused for every /ask
    model=settings.llm_model,  # from .env LLM_MODEL
    api_key=settings.chat_api_key,  # from .env CHAT_API_KEY
    temperature=0,  # deterministic answers for QA
)

_prompt = ChatPromptTemplate.from_messages([  # LCEL prompt: system rules + user question
    ("system", SYSTEM_PROMPT),  # grounded-QA instructions + context slot
    ("human", "{question}"),  # the user's question
])


def _format_docs(docs) -> str:  # turn retrieved Documents into one context string
    return "\n\n".join(  # blank line between chunks so the model can separate them
        f"[{i}] (page {doc.metadata.get('page', '?')}) {doc.page_content}"  # cite page for the model
        for i, doc in enumerate(docs, start=1)  # 1-based labels in the prompt
    )


def _make_sources(docs) -> list[dict]:  # JSON the frontend shows under the answer
    return [  # one dict per retrieved chunk
        {"page": d.metadata.get("page"), "snippet": d.page_content[:200]}  # short preview, not full chunk
        for d in docs  # same docs the retriever returned
    ]


def ask(collection: str, question: str, k: int = 4) -> dict:  # used by POST /ask
    """Retrieve context, prompt the LLM, return {answer, sources}.

    Uses the standard LCEL RAG pattern (CampusX Video 14):
        question -> {context: retriever|format_docs, question: passthrough}
                -> prompt -> llm -> parser
    """  # documents the chain shape
    retriever = get_retriever(collection, k=k)  # load FAISS and take top-k
    chain = (  # LCEL: dict of inputs → prompt → Groq → string
        {
            "context": retriever | _format_docs,  # search, then stringify chunks
            "question": RunnablePassthrough(),  # the raw question string
        }
        | _prompt  # fill {context} and {question}
        | _llm  # call Groq
        | StrOutputParser()  # drop message metadata, keep text
    )

    # We invoke the chain for the answer, then the retriever again to grab
    # the source docs we display to the user.
    answer = chain.invoke(question)  # run retrieval + generation
    docs = retriever.invoke(question)  # same search so the UI can show sources
    return {"answer": answer, "sources": _make_sources(docs)}  # payload for FastAPI / Streamlit