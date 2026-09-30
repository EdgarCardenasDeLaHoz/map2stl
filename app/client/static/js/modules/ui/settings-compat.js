/**
 * modules/ui/settings-compat.js — upgrade saved settings to the current keys.
 *
 * Pure (no DOM, no window), imported by presets.js and by vitest.
 *
 * Region settings and user presets are stored verbatim (server
 * region_settings table, localStorage), so blobs written by older clients
 * keep their old key names. The client only sends and reads the current
 * names, so the server can drop the aliases.
 *
 *   projection.clip_nans -> projection.clip_valid_region   (renamed 2026-09)
 */

/**
 * Return a copy of a grouped settings blob with legacy keys renamed. When
 * both the legacy and the current key are present, the current one wins.
 * Anything that is not a plain settings object is returned unchanged.
 * @param {object|null|undefined} settings
 * @returns {object|null|undefined}
 */
export function normalizeSettingsKeys(settings) {
    const proj = settings?.projection;
    if (!proj || typeof proj !== 'object' || !('clip_nans' in proj)) return settings;
    const { clip_nans: legacy, ...rest } = proj;
    return { ...settings, projection: { clip_valid_region: legacy, ...rest } };
}
