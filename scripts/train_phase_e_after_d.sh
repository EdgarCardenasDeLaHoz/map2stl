#!/bin/bash
# Auto-run Phase E immediately after Phase D completes
# Call this after Phase D finishes

cd "$(dirname "$0")/.." || exit 1

echo "Waiting for Phase D to complete..."
while ! grep -q "Cycle 40/40 metrics:" logs/phase_d_extended.log 2>/dev/null; do
  sleep 5
done

echo ""
echo "=============================================================="
echo "Phase D Complete! Starting Phase E..."
echo "=============================================================="
echo ""

python scripts/train_phase_e_ultra.py
