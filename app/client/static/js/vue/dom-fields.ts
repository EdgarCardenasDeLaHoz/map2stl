/**
 * Read and write the app's form controls by id.
 *
 * The controls' ids are the interface the plain ES modules read (CLAUDE.md rule 3), so the
 * redesigned Beginner panels (F-DESIGN) don't keep their own copy of a setting: they write the
 * existing control and fire input + change, exactly like a user edit, so autosave, rebuilds and
 * every listener run as before.
 */
type Field = HTMLInputElement | HTMLSelectElement;

export function el(id: string): Field | null {
  return document.getElementById(id) as Field | null;
}

export function val(id: string): string | undefined {
  return el(id)?.value;
}

export function num(id: string, fallback: number): number {
  const v = parseFloat(val(id) ?? '');
  return Number.isFinite(v) ? v : fallback;
}

export function checked(id: string): boolean {
  return !!(el(id) as HTMLInputElement | null)?.checked;
}

/** Set a value and fire input + change. No-op when unchanged or missing. */
export function setField(id: string, value: string): void {
  const e = el(id);
  if (!e || e.value === value) return;
  e.value = value;
  e.dispatchEvent(new Event('input', { bubbles: true }));
  e.dispatchEvent(new Event('change', { bubbles: true }));
}

/** Tick a checkbox and fire change. No-op when unchanged or missing. */
export function setChecked(id: string, on: boolean): void {
  const e = el(id) as HTMLInputElement | null;
  if (!e || e.checked === on) return;
  e.checked = on;
  e.dispatchEvent(new Event('change', { bubbles: true }));
}

/** The options of a <select>, for mirroring it in a compact control. */
export function options(id: string): { value: string; label: string; disabled: boolean }[] {
  const e = el(id) as HTMLSelectElement | null;
  return e?.options
    ? [...e.options].map((o) => ({ value: o.value, label: o.text, disabled: o.disabled }))
    : [];
}
