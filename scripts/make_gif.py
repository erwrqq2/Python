"""Record docs/demo.gif: a search on the findex page, typed, run, then a semantic query.

    uv run playwright install chromium           # once
    uv run python scripts/make_gif.py http://127.0.0.1:8000        # or the live URL

Headless Chromium (Playwright) takes screenshots while "typing"; Pillow joins
them into an optimised GIF.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image
from playwright.sync_api import Page, sync_playwright

WIDTH, HEIGHT = 1000, 640
OUT = Path("docs/demo.gif")


class Recorder:
    def __init__(self, page: Page) -> None:
        self.page = page
        self.frames: list[tuple[Image.Image, int]] = []

    def shot(self, ms: int) -> None:
        png = self.page.screenshot(type="png")
        image = Image.open(io.BytesIO(png)).convert("RGB")
        self.frames.append((image.resize((WIDTH * 4 // 5, HEIGHT * 4 // 5)), ms))

    def type(self, selector: str, text: str) -> None:
        box = self.page.locator(selector)
        box.fill("")
        for i, char in enumerate(text, start=1):
            box.type(char)
            if i % 3 == 0 or i == len(text):
                self.shot(90)

    def save(self, path: Path) -> None:
        palette = [f.quantize(colors=128, method=Image.Quantize.MEDIANCUT) for f, _ in self.frames]
        palette[0].save(
            path,
            save_all=True,
            append_images=palette[1:],
            duration=[ms for _, ms in self.frames],
            loop=0,
            optimize=True,
        )


def main(base: str) -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": WIDTH, "height": HEIGHT}, color_scheme="light", device_scale_factor=1
        )
        rec = Recorder(page)
        page.goto(base + "/")
        rec.shot(1400)
        rec.type("input[name=q]", '"science fiction" OR mystery')
        page.keyboard.press("Enter")
        page.wait_for_selector(".results li")
        rec.shot(2600)
        page.mouse.wheel(0, 350)
        page.wait_for_timeout(300)
        rec.shot(1600)
        page.goto(base + "/")
        if page.locator("select[name=mode]").count():
            page.select_option("select[name=mode]", "semantic")
            rec.type("input[name=q]", "a story about a lonely robot")
            page.keyboard.press("Enter")
            page.wait_for_selector(".results li")
            rec.shot(2800)
        page.locator(".results li a.title").first.click()
        page.wait_for_selector(".document")
        rec.shot(2400)
        browser.close()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    rec.save(OUT)
    print(f"{OUT}: {len(rec.frames)} frames, {OUT.stat().st_size / 1024:.0f} KiB")


if __name__ == "__main__":
    main(sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000")
