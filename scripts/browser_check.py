"""Desktop/mobile workflow checks against the deterministic browser test server."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import parse_qs, urlsplit

import requests

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18501")
    parser.add_argument("--backend-url", default="http://127.0.0.1:18000")
    parser.add_argument("--artifacts", type=Path, default=Path("/tmp/papertrail-browser-checks"))
    args = parser.parse_args()
    args.artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="papertrail-pdf-fixture-") as temp:
        fixture = Path(temp) / "research-notes.pdf"
        python = ROOT / "backend/.venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        source = "from pathlib import Path; from tests.helpers import pdf_bytes; import sys; Path(sys.argv[1]).write_bytes(pdf_bytes('Quarterly Research Overview. This document describes a project.', 'The launch code is MARIGOLD. The review date is 14 June.'))"
        subprocess.run([str(python), "-c", source, str(fixture)], cwd=ROOT / "backend", check=True)
        with sync_playwright() as tool:
            browser = tool.chromium.launch(headless=True, args=["--no-sandbox"])
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            page = context.new_page()
            errors = []
            context.on("page", lambda opened: opened.on("pageerror", lambda error: errors.append(str(error))))
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(args.url, wait_until="networkidle")
            expect(page.get_by_text("Read less.", exact=False)).to_be_visible(timeout=30000)
            page.screenshot(path=str(args.artifacts / "empty.png"), full_page=True)
            page.locator('[data-testid="stChatInput"] input[type=file]').set_input_files(str(fixture))
            page.locator("textarea").fill("What is the launch code?")
            page.locator("textarea").press("Enter")
            expect(page.get_by_role("button", name="[1] Page 2", exact=True).first).to_be_visible(timeout=30000)
            expect(page.get_by_test_id("stException")).to_have_count(0)
            page.get_by_role("button", name="[1] Page 2", exact=True).first.click()
            viewer = page.frame_locator('iframe[title$="papertrail_pdf"]')
            expect(viewer.locator("#canvas")).to_be_visible(timeout=30000)
            expect(viewer.locator("#page")).to_have_value("2")
            viewer.get_by_role("button", name="Previous page").click()
            expect(viewer.locator("#page")).to_have_value("1")
            page.get_by_role("button", name="[1] Page 2", exact=True).first.click()
            expect(viewer.locator("#page")).to_have_value("2")
            page.locator("textarea").fill("Explain that further.")
            page.locator("textarea").press("Enter")
            expect(page.get_by_test_id("stChatMessage")).to_have_count(4, timeout=30000)
            expect(page.get_by_test_id("stException")).to_have_count(0)
            expect(page.get_by_role("button", name="Retry", exact=True)).to_have_count(2)
            # Streamlit opens Markdown links in a new tab. Assert its destination,
            # including the exact source/message identity, rather than the old tab.
            with page.expect_popup() as citation_popup:
                page.get_by_role("link", name="[1]", exact=True).last.click()
            cited = citation_popup.value
            expect(cited.get_by_test_id("stChatMessage")).to_have_count(4, timeout=30000)
            expect(cited.frame_locator('iframe[title$="papertrail_pdf"]').locator("#page")).to_have_value("2", timeout=30000)
            assert "citation=1" in cited.url and "message=" in cited.url, cited.url
            expect(cited.get_by_text("REFERENCE [1] · PAGE 2", exact=True)).to_be_visible()
            expect(cited.get_by_test_id("stException")).to_have_count(0)
            cited.close()
            page.reload(wait_until="networkidle")
            expect(page.get_by_test_id("stChatMessage")).to_have_count(4, timeout=30000)
            expect(page.get_by_test_id("stException")).to_have_count(0)
            page.get_by_role("heading", name="research-notes.pdf", exact=True).scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            page.screenshot(path=str(args.artifacts / "desktop.png"), full_page=True)
            # Push this conversation beyond the overview's 40-item window.
            original_url = page.url
            original_id = parse_qs(urlsplit(original_url).query)["chat"][0]
            storage = context.storage_state()
            token = next(item["value"] for origin in storage["origins"] for item in origin["localStorage"]
                         if item["name"].startswith("papertrail.workspace.v1."))
            with requests.Session() as api:
                api.headers["Authorization"] = "Bearer " + token
                original_chat = api.get(f"{args.backend_url}/conversations/{original_id}", timeout=10)
                original_chat.raise_for_status()
                for _ in range(41):
                    response = api.post(f"{args.backend_url}/conversations", timeout=10,
                                        json={"document_id": original_chat.json()["document_id"]})
                    response.raise_for_status()
            page.goto(original_url, wait_until="networkidle")
            expect(page.get_by_test_id("stChatMessage")).to_have_count(4, timeout=30000)
            expect(page.get_by_test_id("stException")).to_have_count(0)
            page.get_by_text("All saved conversations", exact=True).click()
            history_page = page.get_by_label("History page", exact=True)
            history_page.fill("3")
            history_page.press("Enter")
            saved_chat = page.get_by_role("button", name="What is the launch code?", exact=True)
            expect(saved_chat).to_be_visible(timeout=30000)
            search = page.get_by_label("Search titles or filenames", exact=True)
            search.fill("launch code")
            search.press("Enter")
            expect(history_page).to_have_value("1", timeout=30000)
            expect(saved_chat).to_be_visible(timeout=30000)
            saved_chat.click()
            expect(page.get_by_test_id("stChatMessage")).to_have_count(4, timeout=30000)
            page.set_viewport_size({"width": 390, "height": 844})
            page.reload(wait_until="networkidle")
            expect(page.locator("textarea")).to_be_visible(timeout=30000)
            expect(page.get_by_test_id("stChatMessage")).to_have_count(4)
            expect(page.get_by_test_id("stException")).to_have_count(0)
            overflow = page.evaluate("document.documentElement.scrollWidth > innerWidth")
            assert not overflow, "Mobile page has horizontal overflow"
            page.get_by_role("heading", name="research-notes.pdf", exact=True).scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            page.screenshot(path=str(args.artifacts / "mobile.png"), full_page=True)
            page.get_by_role("button", name="[1] Page 2", exact=True).last.click()
            expect(page.frame_locator('iframe[title$="papertrail_pdf"]').locator("#page")).to_have_value("2", timeout=30000)
            with page.expect_popup() as mobile_popup:
                page.get_by_role("link", name="[1]", exact=True).last.click()
            mobile_citation = mobile_popup.value
            mobile_citation.set_viewport_size({"width": 390, "height": 844})
            expect(mobile_citation.frame_locator('iframe[title$="papertrail_pdf"]').locator("#page")).to_have_value("2", timeout=30000)
            expect(mobile_citation.frame_locator('iframe[title$="papertrail_pdf"]').locator("#canvas")).to_be_visible(timeout=30000)
            expect(mobile_citation.get_by_test_id("stException")).to_have_count(0)
            mobile_citation.close()
            assert not errors, errors
            print("PASS — upload/question, follow-ups, exact citation popups, older-chat restoration/search/pagination, refresh and mobile layout")
            browser.close()


if __name__ == "__main__":
    main()
