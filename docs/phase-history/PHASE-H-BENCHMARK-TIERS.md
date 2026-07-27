# Phase H Benchmark Tiers — Definition & Assessment

## Overview

Phase H training will be evaluated against three **benchmark tiers** to determine promotion eligibility and deployment strategy. These tiers represent increasing levels of generalization and robustness.

---

## Tier Definitions

### Tier A: Best-of-Best (Holdout Excellence)
**Threshold**: Global MAE **< 7.0m**, no regional falloff

**What it means**:
- Model achieves strong performance on held-out validation data
- Consistent across EU, US, and Cartagena regions
- Indicates learned features generalize beyond training distribution

**Promotion decision**:
- ✅ **Automatic promotion** — Deploy as production model
- Rationale: Exceeds all regional thresholds with margin

**Recent baseline**:
- Phase G Stage 4: 7.55m MAE (borderline Tier C)
- Phase G High-res was attempted to reach Tier A

---

### Tier B: Cross-City Generalization
**Threshold**: Global MAE **< 7.5m**, all regional MAE **< 7.5m**

**What it means**:
- Model generalizes well across diverse geographies
- No regional regression (e.g., US doesn't collapse to 8m+)
- Suitable for geographic scale-up beyond training geographies

**Promotion decision**:
- ✅ **Conditional promotion** — Deploy with regional monitoring
- Rationale: Strong generalization with slight regional unevenness
- Action: Monitor Tier B regional slices weekly; escalate if any region > 7.5m

**Recent baseline**:
- Phase G Stage 4: 7.55m global but mixed regional performance

---

### Tier C: Baseline Acceptable
**Threshold**: Global MAE **< 8.0m**

**What it means**:
- Model performs acceptably on average
- May have regional weak spots (e.g., dense urban 8.5m)
- Represents "usable but not production-optimized"

**Promotion decision**:
- ⚠️ **Optional promotion** — Deploy for secondary use cases
- Rationale: Acceptable for applications with ~8m tolerance
- Action: Consider as fallback if Tier A/B not achieved; keep Phase G as primary

---

### Below Tier C: Regression
**Threshold**: Global MAE **≥ 8.0m**

**What it means**:
- Model did not improve over warmstart
- Training did not converge or strategy was ineffective

**Promotion decision**:
- ❌ **No promotion** — Reject Phase H result
- Action: Keep Phase G (7.55m); investigate failure cause for next iteration

---

## Stratified Assessment

### By Building Height
Phase H is particularly sensitive to **tall buildings** (20m+) because:
- Sparse in training data
- High error variance
- Predictions collapse toward mean height

**Assessment**:
- Low (0-5m): Expected <6.5m MAE
- Medium (5-20m): Expected <7.5m MAE
- High (20-100m): Expected <9.0m MAE (tolerance relaxed)
- Very High (100m+): Expected <12m MAE (extreme tolerance)

**Failure signals**:
- Low height MAE > 7.5m ← indicates poor shallow building learning
- Medium height MAE > 8.5m ← indicates weak mid-range generalization
- High/VeryHigh collapse to >15m ← indicates tall building regression

---

### By Geographic Region

#### EU (Primary Training Domain)
- 462 tiles across 20+ cities
- **Expected MAE**: <7.0m (most data, lowest error expected)
- **Threshold**: Must be <7.5m for Tier A/B promotion

#### US (Secondary Domain)
- 81 tiles across 10 cities (less diverse terrain than EU)
- **Expected MAE**: <7.5m (some transfer loss)
- **Threshold**: Must be <8.0m for Tier B; <8.5m for Tier C

#### Cartagena (Tertiary, Specialized)
- 30 tiles, dense historic center + tropical climate
- **Expected MAE**: <8.5m (extreme geographic diversity)
- **Threshold**: Must be <9.0m for any tier (special case)

**Regional regression is a failure signal**:
- If EU_MAE ≤ 7.0m but US_MAE ≥ 8.5m → indicates geographic overfitting
- Action: Reweight training or increase US sample allocation

---

## Tier A Assessment Checklist

For automatic promotion, ALL of these must be true:

- [ ] Global MAE **< 7.0m**
- [ ] EU region MAE **< 7.2m**
- [ ] US region MAE **< 7.5m**
- [ ] Cartagena region MAE **< 8.5m**
- [ ] Low height (0-5m) MAE **< 6.5m**
- [ ] Medium height (5-20m) MAE **< 7.5m**
- [ ] High height (20-100m) MAE **< 9.5m**
- [ ] No regional height bin failure (see table below)
- [ ] Training stability: no divergence, monotonic improvement over cycles
- [ ] Gradient freezing effective: new neurons learned independently

---

## Regional × Height Bin Matrix

| Region | 0-5m | 5-20m | 20-100m | 100m+ |
|---|---|---|---|---|
| **EU** | 6.2m | 7.2m | 8.8m | 11m |
| **US** | 6.8m | 7.5m | 9.2m | 12m |
| **Cartagena** | 7.5m | 8.2m | 10.2m | 13m |

**Interpretation**: Each cell is an expected target for Tier A. Any cell > threshold = potential failure signal requiring investigation.

---

## Post-Phase H Decision Tree

```
Phase H Training Complete
  ↓
Extract metrics (global, regional, by height bin)
  ↓
Run stratified_eval.py → JSON report
  ↓
Global MAE?
  ├─ < 7.0m?
  │  ├─ YES: Check regional matrix
  │  │  ├─ All green?: TIER A → Promote
  │  │  └─ Some yellow/red?: Investigate + TIER B
  │  └─ NO (7.0-7.5m?): Check regional matrix
  │     ├─ All < 7.5m?: TIER B → Conditional promote
  │     └─ Some ≥ 7.5m?: TIER C or reject
  └─ ≥ 8.0m?: REJECT (keep Phase G 7.55m)
```

---

## Deployment Strategy by Tier

### Tier A Deployment
- **Primary production model**
- Deploy to all systems
- Monitor: weekly regional slices
- SLA: <7.2m global, <7.5m any region

### Tier B Deployment
- **Production with regional weighting**
- Deploy main; add confidence score per region
- Monitor: daily regional slices + build height bins
- SLA: <7.5m global, <8.0m any region
- Fallback: revert to Phase G if Tier B breached

### Tier C Deployment
- **Secondary/specialty applications only**
- E.g., low-accuracy bulk rendering, teaching demos
- Phase G remains primary
- Monitor: weekly sanity checks only

### Below Tier C
- **Archive Phase H result**
- Investigate root cause (data quality, hyperparams, growth timing)
- Keep Phase G (7.55m) as production
- Plan Phase I with different strategy

---

## Phase H Expected Outcomes

### Optimistic (Tier A)
- Global MAE: 6.8–7.0m (5–10% improvement)
- Growth events: 2–4 cycles (architecture expands to ~[7,8,7,9,8,8,8,8,10] or similar)
- Convergence: Cycle 15–20 (plateaus and ablation stabilizes)

### Conservative (Tier B)
- Global MAE: 7.2–7.5m (4–5% improvement)
- Growth events: 1–2 cycles (modest expansion)
- Regional split: EU < 7.2m, US 7.5–8.0m

### Worst Case (Below Tier C)
- Global MAE: > 8.0m (no improvement or regression)
- Growth never triggers (no plateau detected)
- Indicates dataset quality, crop size, or LR issues
- Action: Investigate data and replan Phase I

