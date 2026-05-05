"""Playwright UI audit script — run from strm2stl/ directory."""
import asyncio
import json
from playwright.async_api import async_playwright

OVERFLOW_JS = """() => {
    const els = document.querySelectorAll('*');
    const issues = [];
    for (const el of els) {
        if (el.scrollWidth > el.clientWidth + 5 || el.scrollHeight > el.clientHeight + 5) {
            const tag = el.tagName + (el.id ? '#'+el.id : '') + (el.className ? '.'+Array.from(el.classList).join('.') : '');
            issues.push(tag.slice(0, 80));
        }
    }
    return issues.slice(0, 20);
}"""

UNLABELED_JS = """() => {
    const inputs = document.querySelectorAll('input,select,textarea');
    const issues = [];
    for (const inp of inputs) {
        const id = inp.id;
        const label = id && document.querySelector('label[for="' + id + '"]');
        const ariaLabel = inp.getAttribute('aria-label');
        const ariaLabelledBy = inp.getAttribute('aria-labelledby');
        if (!label && !ariaLabel && !ariaLabelledBy) {
            issues.push(inp.outerHTML.slice(0, 120));
        }
    }
    return issues.slice(0, 20);
}"""

MISSING_ALT_JS = """() => {
    const imgs = document.querySelectorAll('img');
    const issues = [];
    for (const img of imgs) {
        if (!img.hasAttribute('alt')) {
            issues.push(img.outerHTML.slice(0, 120));
        }
    }
    return issues;
}"""

CONTRAST_JS = """() => {
    function getLuminance(r, g, b) {
        const a = [r, g, b].map(v => {
            v /= 255;
            return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
        });
        return a[0] * 0.2126 + a[1] * 0.7152 + a[2] * 0.0722;
    }
    function parseColor(color) {
        const m = color.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
        return m ? [parseInt(m[1]), parseInt(m[2]), parseInt(m[3])] : null;
    }
    const issues = [];
    const els = document.querySelectorAll('p,span,label,button,a,h1,h2,h3,h4,li');
    for (const el of els) {
        const style = window.getComputedStyle(el);
        const fg = parseColor(style.color);
        const bg = parseColor(style.backgroundColor);
        if (!fg || !bg) continue;
        if (bg[3] === 0) continue;
        const l1 = getLuminance(...fg);
        const l2 = getLuminance(...bg);
        const ratio = (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
        if (ratio < 3.0 && ratio > 0) {
            const tag = el.tagName + (el.id ? '#'+el.id : '') + ' text=' + el.innerText.slice(0,30);
            issues.push({ tag: tag.slice(0,80), ratio: ratio.toFixed(2) });
        }
    }
    return issues.slice(0, 15);
}"""

async def audit():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(viewport={"width": 1280, "height": 900})
        page = await ctx.new_page()

        console_errors = []
        console_warnings = []
        page_errors = []
        network_failures = []

        page.on("console", lambda msg: (
            console_errors.append(msg.text) if msg.type == "error" else
            console_warnings.append(msg.text) if msg.type == "warning" else None
        ))
        page.on("pageerror", lambda err: page_errors.append(str(err)))
        page.on("requestfailed", lambda req: network_failures.append(f"{req.method} {req.url} — {req.failure}"))

        print("=== Loading http://localhost:9000 ===")
        await page.goto("http://localhost:9000", wait_until="networkidle", timeout=30000)
        await asyncio.sleep(2)

        title = await page.title()
        print(f"Title: {title}")
        print(f"URL: {page.url}")

        # Headings
        headings = await page.query_selector_all("h1,h2,h3")
        for h in headings:
            text = await h.inner_text()
            print(f"  Heading: {text[:80]}")

        # Console errors
        print(f"\n--- Console errors ({len(console_errors)}) ---")
        for e in console_errors[:15]:
            print(f"  ERR: {e[:200]}")

        print(f"\n--- Console warnings ({len(console_warnings)}) ---")
        for w in console_warnings[:10]:
            print(f"  WARN: {w[:200]}")

        print(f"\n--- Page errors ({len(page_errors)}) ---")
        for e in page_errors[:10]:
            print(f"  {e[:200]}")

        print(f"\n--- Network failures ({len(network_failures)}) ---")
        for f in network_failures[:10]:
            print(f"  {f[:200]}")

        # Screenshot
        await page.screenshot(path="tools/ml/audit_screenshot.png", full_page=True)
        print("\n[Screenshot saved: tools/ml/audit_screenshot.png]")

        # Broken images
        imgs = await page.query_selector_all("img")
        broken = []
        for img in imgs:
            src = await img.get_attribute("src")
            nat_w = await img.evaluate("el => el.naturalWidth")
            if nat_w == 0 and src:
                broken.append(src)
        print(f"\n--- Broken images ({len(broken)}) ---")
        for b in broken:
            print(f"  {b}")

        # Missing alt attributes
        missing_alt = await page.evaluate(MISSING_ALT_JS)
        print(f"\n--- Images missing alt= ({len(missing_alt)}) ---")
        for m in missing_alt[:10]:
            print(f"  {m}")

        # Inputs without labels
        unlabeled = await page.evaluate(UNLABELED_JS)
        print(f"\n--- Inputs without labels ({len(unlabeled)}) ---")
        for u in unlabeled:
            print(f"  {u}")

        # Layout overflow
        overflow = await page.evaluate(OVERFLOW_JS)
        print(f"\n--- Overflow elements ({len(overflow)}) ---")
        for o in overflow:
            print(f"  {o}")

        # Low contrast
        low_contrast = await page.evaluate(CONTRAST_JS)
        print(f"\n--- Low contrast elements ({len(low_contrast)}) ---")
        for lc in low_contrast:
            print(f"  ratio={lc['ratio']}  {lc['tag']}")

        # Buttons
        buttons = await page.query_selector_all("button")
        enabled = []
        disabled_btns = []
        for btn in buttons:
            text = (await btn.inner_text()).strip()
            is_disabled = await btn.get_attribute("disabled")
            if is_disabled is not None:
                disabled_btns.append(text[:40])
            else:
                enabled.append(text[:40])
        print(f"\n--- Buttons: {len(enabled)} enabled, {len(disabled_btns)} disabled ---")
        for b in enabled[:20]:
            print(f"  [enabled] {b}")
        for b in disabled_btns[:10]:
            print(f"  [disabled] {b}")

        # Tab navigation — check other tabs if they exist
        tabs = await page.query_selector_all("[role=tab], .tab, nav a, .nav-tab")
        print(f"\n--- Tabs/nav items found: {len(tabs)} ---")
        for tab in tabs:
            text = (await tab.inner_text()).strip()
            print(f"  Tab: {text[:60]}")

        await browser.close()
        print("\n=== Audit complete ===")

asyncio.run(audit())
