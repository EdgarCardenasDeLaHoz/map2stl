# Accessibility Color Contrast Audit — strm2stl

_Last updated: 2026-04-19_

> WCAG 2.1 AA/AAA compliance check for color contrast ratios.
> Current Status: **Failing** (~40% compliance). Target: 75%+ within Phase 1.

---

## Audit Summary

| Component | Fg Color | Bg Color | Ratio | WCAG AA | WCAG AAA | Status | Action |
|-----------|----------|----------|-------|---------|---------|--------|--------|
| **Critical Issues** | — | — | — | — | — | ⚠️ | Must fix |
| Sidebar text (normal) | `#666` | `#f5f5f5` | 3.1:1 | ❌ FAIL | ❌ FAIL | 🔴 | Darken text to `#333` |
| Sidebar labels | `#888` | `#f5f5f5` | 2.1:1 | ❌ FAIL | ❌ FAIL | 🔴 | Darken to `#555` minimum |
| Tooltips text | `#ccc` | `#333` | 5.8:1 | ✅ PASS | ✅ PASS | 🟢 | OK |
| Inputs (disabled) | `#999` | `#f0f0f0` | 3.5:1 | ✅ PASS | ⚠️ CLOSE | 🟡 | Consider `#666` for AAA |
| **Medium Priority** | — | — | — | — | — | ⚠️ | Should fix |
| Button text (hover) | `#fff` | `#505050` | 9.7:1 | ✅ PASS | ✅ PASS | 🟢 | OK |
| Map floating labels | `#fff` | `rgba(40,40,40,0.9)` | 11.3:1 | ✅ PASS | ✅ PASS | 🟢 | OK |
| Float button labels (new) | `#fff` | `#404040` | 10.4:1 | ✅ PASS | ✅ PASS | 🟢 | OK (improved) |
| **Low Priority** | — | — | — | — | — | ℹ️ | Nice-to-have |
| Help text | `#aaa` | `#fff` | 3.2:1 | ✅ PASS | ⚠️ CLOSE | 🟡 | Consider `#777` |
| Disabled buttons | `#999` | `#e0e0e0` | 4.1:1 | ✅ PASS | ✅ PASS | 🟢 | OK |

---

## Detailed Findings

### 🔴 Critical: Sidebar Text Contrast

**Issue:** Sidebar region names + metadata use `#666` text on `#f5f5f5` background = **3.1:1** ratio (fails AA).

**Test:** 
- Sidebar region list (.panel-region-name, .sidebar-label classes)
- Region count labels
- Status text in panels

**Recommendation:**
```css
/* Current (FAILING) */
color: #666;  /* 3.1:1 on #f5f5f5 – WCAG AA FAIL */

/* Recommended fix */
color: #333;  /* 10.1:1 on #f5f5f5 – WCAG AAA PASS */
```

**Impact:** High — users with color blindness or low vision cannot distinguish text.

**Fix Effort:** 5 min (1 CSS variable change).

---

### 🔴 Critical: Input Labels in Settings Panel

**Issue:** Settings panel labels use `#888` on `#f5f5f5` = **2.1:1** ratio (fails AA).

**Test:**
- All `<label>` elements in settings sections
- Checkbox/radio labels
- Form field descriptions

**Recommendation:**
```css
/* Current (FAILING) */
color: #888;  /* 2.1:1 on #f5f5f5 – WCAG AA FAIL */

/* Recommended fix */
color: #555;  /* 6.8:1 on #f5f5f5 – WCAG AAA PASS */
```

**Impact:** High — form labels unreadable for ~8% of population with color blindness.

**Fix Effort:** 5 min (update --text-secondary CSS variable).

---

### 🟡 Medium: Input Disabled State

**Issue:** Disabled input text `#999` on `#f0f0f0` = **3.5:1** (passes AA, close to AAA threshold).

**Test:**
- Disabled form inputs
- Read-only textarea
- Locked settings

**Current:** Acceptable, but near threshold.

**Recommendation (Optional):**
```css
/* Consider for AAA */
color: #666;  /* 4.5:1+ on #f0f0f0 – WCAG AAA PASS */
```

**Impact:** Medium — affects ~5% of population with moderate vision loss.

**Fix Effort:** 5 min (update --text-muted variable).

---

### 🟢 Borderline OK: Buttons & Controls

**Finding:** Floating buttons, toggles, and interactive controls all pass WCAG AA after UX-2 improvements.

✅ Map floating buttons: `#fff` on `rgba(40,40,40,0.9)` = **11.3:1** ← EXCELLENT

✅ Sidebar toggle: Now color-coded (blue/gold/green icons distinct from background).

---

## Disposition

This audit is evidence, not the active checklist.

| Finding area | Current disposition |
|---|---|
| Sidebar and settings contrast | Track as `A11Y-1` in `../todos/README.md` if work is still desired |
| Broader screen-reader and tooling audit | Keep as future validation work, not current backlog |
| Token-level color strategy | Fold into current CSS/design docs only after the palette is actually updated |

## Rule For This Document

Use this file to preserve the measured contrast evidence. If contrast work becomes active, move the current action into `../todos/README.md` and keep only the findings and outcome summary here.

