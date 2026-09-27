/**
 * modules/dem/dem-sampling.js — wording for the DEM response's
 * `source_resolution` block (F-UX batch 2).
 *
 * The server reports how many real samples of the chosen source span the box
 * (geo2stl.dem.dem_sampling); the Edit tab (DemSamplingInfo.vue) shows it as
 * "~30 m → 165×165 real samples, upsampled to 600×600" and warns when most of
 * the grid is interpolation.
 */

/** Upsampling beyond this factor gets a warning. */
export const UPSAMPLE_WARN = 4;

/**
 * @param {{native_resolution_m:number, native_samples:[number,number],
 *          grid:[number,number], upsample:number}|null|undefined} info
 * @returns {{text:string, warn:boolean, warning:string}|null}
 */
export function describeDemSampling(info) {
    if (!info || !Array.isArray(info.native_samples) || !Array.isArray(info.grid)) return null;
    const [nr, nc] = info.native_samples;
    const [gr, gc] = info.grid;
    const res = Math.round(info.native_resolution_m);
    const factor = Number(info.upsample) || Math.max(gr / nr, gc / nc);
    let verb;
    if (factor > 1.05) verb = 'upsampled to';
    else if (factor < 0.95) verb = 'downsampled to';
    else verb = 'returned as';
    const text = `~${res} m → ${nc}×${nr} real samples, ${verb} ${gc}×${gr}`;
    const warn = factor > UPSAMPLE_WARN;
    // Only a coarser-than-30 m source has a finer one to switch to.
    const advice = res > 30
        ? 'A 30 m source (SRTM / Copernicus) adds real detail; a lower Resolution loses none.'
        : 'This is the finest source; a lower Resolution loses no real detail.';
    const warning = warn
        ? `${factor.toFixed(1)}× upsampled: most of the grid is interpolated. ${advice}`
        : '';
    return { text, warn, warning };
}
