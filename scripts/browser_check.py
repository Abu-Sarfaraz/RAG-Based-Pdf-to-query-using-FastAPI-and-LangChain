"""Desktop/mobile workflow checks against the deterministic browser test server."""
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:18501")
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
            page.get_by_role("link", name="[1]", exact=True).last.click()
            expect(page.get_by_test_id("stChatMessage")).to_have_count(4, timeout=30000)
            expect(page.frame_locator('iframe[title$="papertrail_pdf"]').locator("#page")).to_have_value("2", timeout=30000)
            page.reload(wait_until="networkidle")
            expect(page.get_by_test_id("stChatMessage")).to_have_count(4, timeout=30000)
            expect(page.get_by_test_id("stException")).to_have_count(0)
            page.get_by_role("heading", name="research-notes.pdf", exact=True).scroll_into_view_if_needed()
            page.wait_for_timeout(500)
            page.screenshot(path=str(args.artifacts / "desktop.png"), full_page=True)
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
            assert not errors, errors
            print("PASS — combined upload/question, follow-up history, citation navigation, browser restoration, and mobile layout")
            browser.close()


if __name__ == "__main__":
    main()
