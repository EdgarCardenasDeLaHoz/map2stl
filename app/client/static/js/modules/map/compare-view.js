/**
 * modules/compare-view.js — Inline side-by-side layer compare in the Edit view
 * (#compareInlineContainer, DemContainer.vue).
 *
 * Public API (all on window):
 *   updateCompareCanvases()         — wire the layer selects (once) + render both sides
 *
 * renderCompareLayer(side) copies a layer canvas into one compare panel.
 */

// ─────────────────────────────────────────────────────────────────────────────
// Inline compare (simple canvas copy)
// ─────────────────────────────────────────────────────────────────────────────

function initCompareMode() {
    const leftSel = document.getElementById('compareInlineLeft');
    const rightSel = document.getElementById('compareInlineRight');
    if (leftSel && !leftSel._wired) {
        leftSel._wired = true;
        leftSel.addEventListener('change', () => renderCompareLayer('left'));
        rightSel.addEventListener('change', () => renderCompareLayer('right'));
    }
}

function renderCompareLayer(side) {
    const cap = side.charAt(0).toUpperCase() + side.slice(1);
    const select = document.getElementById(`compareInline${cap}`);
    const canvas = document.getElementById(`compareInline${cap}Canvas`);
    if (!select || !canvas) return;

    const sourceSelectors = {
        dem: `#demImage ${window.DEM_CANVAS_SELECTOR}`,
        water: '#waterMaskImage canvas',
        sat: '#satelliteImage canvas',
        combined: '#combinedImage canvas',
    };
    const srcCanvas = document.querySelector(sourceSelectors[select.value]);
    const ctx = canvas.getContext('2d');

    if (!srcCanvas || !srcCanvas.width || !srcCanvas.height) {
        canvas.width = 300; canvas.height = 150;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        ctx.fillStyle = '#444'; ctx.font = '13px sans-serif'; ctx.textAlign = 'center';
        ctx.fillText('Load this layer first', 150, 80);
        return;
    }
    canvas.width = srcCanvas.width;
    canvas.height = srcCanvas.height;
    ctx.drawImage(srcCanvas, 0, 0);
}

function updateCompareCanvases() {
    initCompareMode();
    renderCompareLayer('left');
    renderCompareLayer('right');
}

// ─────────────────────────────────────────────────────────────────────────────
// Expose on window
// ─────────────────────────────────────────────────────────────────────────────

window.updateCompareCanvases = updateCompareCanvases;
