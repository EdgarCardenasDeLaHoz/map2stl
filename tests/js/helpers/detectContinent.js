/**
 * Pure extraction of detectContinent from regions/region-ui.js.
 * Source: app/client/static/js/modules/regions/region-ui.js:48–61
 */
export function detectContinent(lat, lon) {
    if (lat < -60) return 'Antarctica';
    if (lat >= -55 && lat <= -10 && lon >= 110 && lon <= 180) return 'Oceania';
    if (lat >= -10 && lat <= 0 && lon >= 130 && lon <= 180) return 'Oceania';
    if (lat >= -56 && lat <= 13 && lon >= -82 && lon <= -34) return 'South America';
    if (lat >= 13 && lat <= 75 && lon >= -168 && lon <= -52) return 'North America';
    if (lat >= 8 && lat <= 28 && lon >= -90 && lon <= -52) return 'North America';
    if (lat >= 55 && lon >= 26 && lon <= 180) return 'Asia';
    if (lat >= -11 && lat <= 55 && lon >= 60 && lon <= 145) return 'Asia';
    if (lat >= 25 && lat <= 43 && lon >= 35 && lon <= 60) return 'Asia';
    if (lat >= -37 && lat <= 38 && lon >= -18 && lon <= 52) return 'Africa';
    if (lat >= 35 && lat <= 72 && lon >= -25 && lon <= 45) return 'Europe';
    return 'Other';
}
