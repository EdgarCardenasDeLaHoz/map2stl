"""Analyze growth degradation patterns to identify root causes and solutions.

Examines:
1. Epoch-1 jump at each growth transition
2. Recovery rate through training cycle
3. First-epoch batch norm statistics mismatch
4. New channel activation patterns
"""

import re
from pathlib import Path
from collections import defaultdict


def analyze_growth_jumps(log_file: str) -> dict:
    """Extract cycle boundaries and first-epoch metrics."""
    cycles = []
    current_cycle = None
    
    with open(log_file, 'r') as f:
        for line in f:
            # Detect cycle header
            m = re.search(r'=== Cycle (\d+)/\d+.*channels=\[([\d,\s]+)\].*params=([\d,]+)', line)
            if m:
                if current_cycle:
                    cycles.append(current_cycle)
                current_cycle = {
                    'cycle': int(m.group(1)),
                    'channels': [int(x.strip()) for x in m.group(2).split(',')],
                    'params': int(m.group(3).replace(',', '')),
                    'epoch_1': None,
                    'best_val': None,
                    'best_epoch': None,
                }
                continue
            
            # Detect first epoch
            if current_cycle and current_cycle['epoch_1'] is None:
                m = re.search(r'ep\s+1/\d+.*val=([\d.]+)', line)
                if m:
                    current_cycle['epoch_1'] = float(m.group(1))
                    continue
            
            # Detect cycle best
            m = re.search(r'Cycle \d+ metrics:.*best_val=([\d.]+)', line)
            if m and current_cycle:
                current_cycle['best_val'] = float(m.group(1))
                continue
    
    if current_cycle:
        cycles.append(current_cycle)
    
    return cycles


def compute_growth_analysis(cycles: list[dict]) -> dict:
    """Analyze growth patterns."""
    analysis = {
        'growth_jumps': [],  # (cycle_from, cycle_to, jump_magnitude, recovery_rate)
        'cycle_summaries': [],
    }
    
    for i, cycle in enumerate(cycles):
        summary = {
            'cycle': cycle['cycle'],
            'channels': cycle['channels'],
            'epoch_1_val': cycle['epoch_1'],
            'best_val': cycle['best_val'],
        }
        
        if i > 0:
            prev_best = cycles[i-1]['best_val']
            curr_epoch_1 = cycle['epoch_1']
            curr_best = cycle['best_val']
            
            if prev_best is not None and curr_epoch_1 is not None and curr_best is not None:
                jump = curr_epoch_1 - prev_best
                recovery = prev_best - curr_best  # negative means recovered worse than prev
                
                analysis['growth_jumps'].append({
                    'cycle': cycle['cycle'],
                    'jump': jump,
                    'recovery': recovery,
                    'final_vs_prev': curr_best - prev_best,
                })
                
                summary['jump_from_prev'] = jump
                summary['recovery'] = recovery
        
        analysis['cycle_summaries'].append(summary)
    
    return analysis


def print_analysis(analysis: dict):
    """Print detailed analysis."""
    print("=" * 80)
    print("GROWTH DEGRADATION ANALYSIS")
    print("=" * 80)
    print()
    
    # Cycle-by-cycle table
    print("Cycle-by-Cycle Metrics:")
    print("-" * 80)
    print("Cycle | Channels         | Epoch-1 Val | Best Val  | Jump  | Recovery | Status")
    print("-" * 80)
    
    for s in analysis['cycle_summaries']:
        ch_str = str(s['channels'])[:16].ljust(16)
        ep1 = f"{s['epoch_1_val']:.4f}" if s['epoch_1_val'] else "?"
        best = f"{s['best_val']:.4f}" if s['best_val'] else "?"
        
        jump_str = "—"
        recovery_str = "—"
        status = "baseline"
        
        if 'jump_from_prev' in s:
            jump = s['jump_from_prev']
            recovery = s['recovery']
            jump_str = f"{jump:+.4f}"
            recovery_str = f"{recovery:+.4f}"
            
            if jump > 0.02:
                status = "⚠ large-jump"
            elif recovery > 0.005:
                status = "⚠ degraded"
            elif recovery < -0.005:
                status = "✓ improved"
            else:
                status = "~ stable"
        
        print(f"{s['cycle']:5d} | {ch_str} | {ep1:11s} | {best:9s} | {jump_str:6s} | {recovery_str:8s} | {status}")
    
    print()
    print("INTERPRETATION:")
    print("-" * 80)
    
    if not analysis['growth_jumps']:
        print("No growth jumps detected.")
        return
    
    # Analyze jump magnitudes
    jumps = [g['jump'] for g in analysis['growth_jumps']]
    avg_jump = sum(jumps) / len(jumps)
    max_jump = max(jumps)
    
    print(f"Average epoch-1 jump after growth: {avg_jump:+.4f}")
    print(f"Maximum epoch-1 jump:              {max_jump:+.4f}")
    
    # Analyze recovery patterns
    recoveries = [g['recovery'] for g in analysis['growth_jumps']]
    avg_recovery = sum(recoveries) / len(recoveries)
    worse_recovery = sum(1 for r in recoveries if r > 0.01)
    
    print(f"Average cycle-end degradation:    {avg_recovery:+.4f}")
    print(f"Cycles with >0.01 degradation:    {worse_recovery}/{len(recoveries)}")
    
    # Identify which cycles have worst first-epoch jumps
    worst_jumps = sorted(enumerate(analysis['growth_jumps'], 1), 
                         key=lambda x: x[1]['jump'], reverse=True)[:3]
    print()
    print("Worst growth-induced epochs:")
    for idx, (cycle_idx, jump_data) in enumerate(worst_jumps, 1):
        print(f"  {idx}. Cycle {jump_data['cycle']}: {jump_data['jump']:+.4f} " +
              f"(recovery={jump_data['recovery']:+.4f})")
    
    print()
    print("POTENTIAL ROOT CAUSES & SOLUTIONS:")
    print("-" * 80)
    print("1. Random initialization of new channels causes immediate training disruption")
    print("   → Solution: Reset batch norm stats or use warm-up epoch after growth")
    print()
    print("2. New channels have high gradients early, destabilizing existing weights")
    print("   → Solution: Scale new channel learning rate differently (e.g., 0.5x)")
    print()
    print("3. Batch norm running statistics stale after architecture change")
    print("   → Solution: Re-compute batch norm statistics on full training set")
    print()
    print("4. Loss landscape changes with new dimensions, epoch 1 is exploratory")
    print("   → Solution: Use gradient clipping or smaller LR for first N epochs after growth")
    print()


if __name__ == '__main__':
    log_file = Path(__file__).parent.parent.parent / 'logs' / 'retna_grow_continue.log'
    
    if not log_file.exists():
        print(f"Log file not found: {log_file}")
        print("Training may still be in progress...")
        exit(1)
    
    cycles = analyze_growth_jumps(str(log_file))
    analysis = compute_growth_analysis(cycles)
    print_analysis(analysis)
