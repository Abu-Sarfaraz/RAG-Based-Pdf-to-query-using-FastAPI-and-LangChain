"""Live quality/latency smoke evaluation on synthetic PDFs, using temporary storage."""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from fastapi.testclient import TestClient
from app.config import settings
from app.main import create_app
from tests.helpers import pdf_bytes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Use the configured Groq key for seven answer requests and a follow-up rewrite.")
    parser.add_argument("--output", type=Path, default=Path("evaluation-results.json"))
    args = parser.parse_args()
    if not args.live:
        parser.error("Pass --live to run the seven-question evaluation against your configured answer service.")
    if not settings.chat_api_key:
        parser.error("Set CHAT_API_KEY before running the live evaluation.")
    report = {"model": settings.llm_model, "cases": []}
    with tempfile.TemporaryDirectory(prefix="papertrail-evaluation-") as directory:
        config = replace(settings, data_dir=Path(directory))
        startup = time.perf_counter()
        with TestClient(create_app(config)) as client:
            report["startup_seconds"] = round(time.perf_counter() - startup, 3)
            token = client.post("/workspaces").json()["token"]
            headers = {"Authorization": "Bearer " + token}
            def upload(filename, data):
                response = client.post("/documents", headers=headers, files={"file": (filename, data, "application/pdf")})
                response.raise_for_status()
                document = response.json()
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    document = client.get("/documents/" + document["id"], headers=headers).json()
                    if document["status"] == "ready":
                        return document
                    if document["status"] == "failed":
                        raise RuntimeError(document["error"])
                    time.sleep(0.1)
                raise RuntimeError("Preparation timed out")
            primary = upload("orbit-study.pdf", pdf_bytes(
                "Project ORBIT Study Brief. The sample included 240 students. The study lasted 12 weeks. The primary outcome was improved reading comprehension. The author was Dr Nadia Rahman.",
                "Project ORBIT Results. The score improved by 18 percent. The follow-up review is scheduled for 14 June 2027. The authors recommend daily reading practice for 20 minutes."))
            secondary = upload("tidal-study.pdf", pdf_bytes("Project TIDAL. The sample included 60 students. The study lasted 4 weeks. The score improved by 5 percent."))
            report["preparation_seconds"] = [primary["preparation_seconds"], secondary["preparation_seconds"]]
            conversations = {document["id"]: client.post("/conversations", headers=headers, json={"document_id": document["id"]}).json()["id"] for document in (primary, secondary)}
            cases = [
                (primary, "How many students participated?", r"240", 1),
                (primary, "How long did the study last?", r"12\s+weeks", 1),
                (primary, "How much did the score improve?", r"18\s*(?:%|percent)", 2),
                (primary, "When is the follow-up review?", r"14\s+June\s+2027", 2),
                (primary, "Explain that date.", r"14\s+June\s+2027", 2),
                (primary, "What was the author's home address?", r"(?:not|doesn't|don't|cannot|isn't|can't).{0,100}(?:provide|mention|specif|know|contain|support|include|available|find|give)", None),
                (secondary, "How many students participated?", r"60", 1),
            ]
            for document, question, expected, page in cases:
                response = client.post(f"/conversations/{conversations[document['id']]}/messages", headers=headers,
                                       json={"question": question, "request_id": uuid.uuid4().hex})
                events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                final = next((event for event in events if event["event"] == "done"), None)
                error = next((event["message"] for event in events if event["event"] == "error"), None)
                passed = bool(final and re.search(expected, final["answer"], re.I | re.S))
                cited_pages = []
                if final:
                    cited_pages = [source["page"] for source in final["sources"] if source["id"] in final.get("cited_source_ids", [])]
                    if page is not None:
                        passed = passed and page in cited_pages
                report["cases"].append({"question": question, "document": document["filename"], "passed": passed,
                                        "answer": final["answer"] if final else None, "error": error,
                                        "cited_pages": cited_pages, "metrics": final.get("metrics", {}) if final else {}})
                print(("PASS" if passed else "FAIL") + " — " + question, flush=True)
            totals = [case["metrics"]["total_seconds"] for case in report["cases"] if "total_seconds" in case["metrics"]]
            first_tokens = [case["metrics"]["first_token_seconds"] for case in report["cases"] if "first_token_seconds" in case["metrics"]]
            report["passed"] = sum(case["passed"] for case in report["cases"])
            report["median_answer_seconds"] = round(statistics.median(totals), 3) if totals else None
            report["median_first_token_seconds"] = round(statistics.median(first_tokens), 3) if first_tokens else None
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Evaluation: {report['passed']}/7 passed; median first token {report['median_first_token_seconds']}s; median answer {report['median_answer_seconds']}s", flush=True)
    return 0 if report["passed"] == 7 else 1


if __name__ == "__main__":
    raise SystemExit(main())
