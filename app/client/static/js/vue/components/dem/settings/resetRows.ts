/**
 * Per-group Reset of the Edit panel (LayerSettings.vue): which control goes back to which value.
 * Pure, so it is tested without a DOM; the panel supplies `probe` (what the page holds for an id)
 * and applies the actions through dom-fields (setField / setChecked fire the usual change events).
 */
import type { Row } from './layerGroups';

/** What a control looks like: kind and the value its markup starts with (null when absent). */
export type Probe = { kind: 'checkbox' | 'select' | 'input'; initial: string | boolean } | null;
export type ResetAction = { id: string; op: 'checked'; value: boolean } | { id: string; op: 'field'; value: string };

export function resetActions(rows: Row[], probe: (id: string) => Probe, detail: string): ResetAction[] {
  const out: ResetAction[] = [];
  for (const r of rows) {
    if (!r.id) continue;
    const p = probe(r.id);
    if (!p) continue;
    // A row with its own getter (Trees: shown off where no lidar covers) still owns a checkbox
    // holding the real setting, so Reset restores that; a getter over any other control stays.
    if (r.get && p.kind !== 'checkbox') continue;
    if (r.kind === 'res') out.push({ id: r.id, op: 'field', value: detail });
    else if (p.kind === 'checkbox') out.push({ id: r.id, op: 'checked', value: p.initial as boolean });
    else out.push({ id: r.id, op: 'field', value: p.initial as string });
  }
  return out;
}
