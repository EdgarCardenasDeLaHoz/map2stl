#!/usr/bin/env python3
"""
Playwright standalone browser connector.
Connects to a Chrome instance launched with --remote-debugging-port enabled.
"""

import asyncio
import sys
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("ERROR: Playwright not installed. Run: pip install playwright")
    sys.exit(1)


async def main():
    debug_port = 9222
    debug_url = f"http://127.0.0.1:{debug_port}"
    app_url = "http://127.0.0.1:9001"
    
    print(f"\n{'='*60}")
    print(f"Playwright Standalone Browser Connector")
    print(f"{'='*60}")
    print(f"Debug URL: {debug_url}")
    print(f"App URL: {app_url}\n")
    
    async with async_playwright() as playwright:
        try:
            # Connect to the running Chrome instance (with retry for startup races)
            print(f"Connecting to Chrome on debug port {debug_port}...", end=" ", flush=True)
            browser = None
            last_error = None
            for _ in range(20):
                try:
                    browser = await playwright.chromium.connect_over_cdp(debug_url)
                    break
                except Exception as e:
                    last_error = e
                    await asyncio.sleep(0.5)

            if browser is None:
                raise RuntimeError(f"Unable to connect to {debug_url}: {last_error}")

            print("✓ Connected!\n")
            
            # Get all pages/tabs
            contexts = browser.contexts
            print(f"Found {len(contexts)} context(s)")
            
            if not contexts:
                print("Creating new page...")
                context = await browser.new_context()
                page = await context.new_page()
            else:
                context = contexts[0]
                pages = context.pages
                if not pages:
                    page = await context.new_page()
                else:
                    page = pages[0]
            
            print(f"Using page: {page.url if page.url else '(blank)'}\n")
            
            # Navigate to app
            print(f"Navigating to {app_url}...")
            await page.goto(app_url, wait_until="networkidle")
            print("✓ Page loaded!\n")
            
            # Example interaction: search for Amazon
            print("="*60)
            print("EXAMPLE: Testing hydrology layer on Amazon region")
            print("="*60)
            
            # Find and fill region search
            print("\n1. Searching for 'Amazon'...")
            await page.evaluate("""
                () => {
                    const input = document.getElementById('coordSearch');
                    if (!input) throw new Error('coordSearch not found');
                    input.value = 'Amazon';
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }
            """)
            await page.wait_for_timeout(500)

            # Click Amazon option via DOM instead of locator strict matching
            clicked_amazon = await page.evaluate("""
                () => {
                    const options = Array.from(document.querySelectorAll('[role="option"]'));
                    const amazon = options.find(opt => (opt.textContent || '').includes('Amazon'));
                    if (!amazon) return false;
                    amazon.click();
                    return true;
                }
            """)
            if clicked_amazon:
                print("2. Clicking Amazon option...")
                await page.wait_for_timeout(500)
            
            # Click Edit tab
            print("3. Switching to Edit view...")
            await page.locator('#tabEdit').click()
            await page.wait_for_timeout(1000)
            
            # Activate the hydrology layer so it auto-loads and draws into the stack.
            print("4. Activating Hydrology layer...")
            await page.evaluate("""
                () => {
                    if (typeof window.setStackMode !== 'function') {
                        throw new Error('window.setStackMode is not available');
                    }
                    window.setStackMode('Hydrology');
                }
            """)
            await page.wait_for_function("() => !!window.appState?.hydrologySourceCanvas", timeout=60000)
            await page.evaluate("""() => window.updateStackedLayers?.()""")
            await page.wait_for_timeout(1000)

            # Take screenshot
            screenshot_path = Path(r"c:\Users\eac84\OneDrive\Documents\Projects\3D Maps\Code\strm2stl\tests\hydrology-render-test.png")
            screenshot_path.parent.mkdir(parents=True, exist_ok=True)
            
            print("5. Taking screenshot...")
            await page.screenshot(path=str(screenshot_path), full_page=False)
            print(f"✓ Screenshot saved: {screenshot_path}\n")
            
            # Sample canvas content
            print("6. Sampling canvas content...")
            canvas_stats = await page.evaluate("""
                async () => {
                    const canvases = {
                        demCanvas: document.getElementById('layerDemCanvas'),
                        hydroCanvas: document.getElementById('layerHydroCanvas'),
                        stackCanvas: document.getElementById('stackViewCanvas'),
                        hydroSourceCanvas: window.appState?.hydrologySourceCanvas || null
                    };
                    
                    const stats = {};
                    for (const [name, canvas] of Object.entries(canvases)) {
                        if (canvas && canvas.width > 0) {
                            const ctx = canvas.getContext('2d');
                            const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
                            let alphaCount = 0;
                            for (let i = 3; i < imgData.data.length; i += 4) {
                                if (imgData.data[i] > 0) alphaCount++;
                            }
                            stats[name] = {
                                width: canvas.width,
                                height: canvas.height,
                                alphaPixels: alphaCount
                            };
                        }
                    }
                    return stats;
                }
            """)
            
            print("\nCanvas Content:")
            for name, stat in canvas_stats.items():
                if stat:
                    print(f"  {name}:")
                    print(f"    Size: {stat['width']} × {stat['height']}")
                    print(f"    Alpha pixels: {stat['alphaPixels']:,}")

            stack_alpha = (canvas_stats.get('stackCanvas') or {}).get('alphaPixels', 0)
            source_alpha = (canvas_stats.get('hydroSourceCanvas') or {}).get('alphaPixels', 0)
            if source_alpha <= 0 and stack_alpha <= 0:
                raise RuntimeError('Hydrology appears blank: both source and stack canvases have zero alpha pixels')
            
            print("\n" + "="*60)
            print("✓ Test completed successfully!")
            print("="*60)
            print("\nLeaving Chrome open for manual exploration and further Playwright runs.")
            print("Press Ctrl+C in this terminal when you are done.\n")

            while True:
                await asyncio.sleep(1)
            
        except Exception as e:
            print(f"\n✗ Error: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


if __name__ == "__main__":
    print("\n" + "▶" * 30)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⊘ Interrupted by user")
