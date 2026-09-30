/**
 * modules/regions/continent.js — heuristic continent for a lat/lon point.
 *
 * Pure (no DOM, no window), so vitest imports it directly. region-ui.js
 * re-exposes it as window.detectContinent for regions.js and the sidebar.
 *
 * Only used to group saved regions in the sidebar, so the boundaries are
 * coarse polylines, good to roughly 0.1-0.5 degrees. Known conventions and
 * limits:
 *   - Africa / Europe: a line through the Mediterranean (Strait of Gibraltar
 *     ~35.95 N, north of the Algerian / Tunisian coast up to Ras ben Sakka
 *     37.35 N, then south of Pantelleria, Malta and Crete, north of Libya and
 *     Egypt). Southern Spain, Sicily, Malta, Crete and the Aegean are Europe.
 *   - Africa / Asia: the Suez Canal, Gulf of Suez, Red Sea and Bab-el-Mandeb.
 *     Sinai, the Levant and Arabia are Asia.
 *   - Europe / Asia: the Bosphorus and Dardanelles (Istanbul's historic
 *     centre, west of the Bosphorus, is Europe; Anatolia is Asia), the Greek
 *     islands are Europe with the Turkish Aegean coast mostly Asia (a few
 *     coastal points such as Cesme land on the wrong side), the Greater
 *     Caucasus crest (Grozny and Derbent Europe; Tbilisi and Baku Asia), the
 *     Caspian, and the Urals at 60 E. Cyprus is Asia.
 */

/**
 * Piecewise-linear interpolation through `points` ([x, y] pairs sorted by x),
 * clamped to the end values outside their range.
 * @param {Array<[number, number]>} points
 * @param {number} x
 * @returns {number}
 */
function _interp(points, x) {
    if (x <= points[0][0]) return points[0][1];
    for (let i = 1; i < points.length; i++) {
        const [x1, y1] = points[i];
        if (x <= x1) {
            const [x0, y0] = points[i - 1];
            return y0 + ((x - x0) / (x1 - x0)) * (y1 - y0);
        }
    }
    return points[points.length - 1][1];
}

// [lon, lat]: Africa lies south of this line (Atlantic off Morocco, then the
// Mediterranean between the African coast and Spain / Sicily / Malta / Crete).
const AFRICA_NORTH_EDGE = [
    [-32, 36.0],
    [-5.6, 35.95],   // Strait of Gibraltar: Tangier 35.77 south, Tarifa 36.01 north
    [-2.0, 36.2],    // Alboran Sea: Almeria 36.84 north, Morocco coast ~35.2 south
    [3.0, 36.95],    // Algiers 36.77
    [8.0, 37.2],     // Annaba 36.9, Cap de Fer 37.08
    [9.8, 37.45],    // Ras ben Sakka 37.35, northernmost point of Africa
    [11.2, 37.2],    // Cap Bon 37.08; Sicily (Marsala 37.8) north
    [11.6, 35.0],    // east of the Tunisian coast; Pantelleria stays north
    [12.5, 34.0],    // Lampedusa and Malta north, Tripoli 32.9 south
    [24.0, 34.0],    // Crete / Gavdos 34.84 north, Cyrenaica 32.9 south
    [36.0, 32.0],    // Alexandria 31.2 south
];

// [lat, lon]: Africa lies west of this line (Bab-el-Mandeb, the Red Sea axis,
// the Gulf of Suez and the Suez Canal). North of its end the canal longitude
// holds, so Sinai and the Levant fall east of it.
const AFRICA_EAST_EDGE = [
    [12.55, 43.38],  // Bab-el-Mandeb
    [15.0, 41.8],    // Massawa 39.45 west, Kamaran 42.6 east
    [20.0, 38.7],    // Port Sudan 37.2 west, Al Lith 40.27 east
    [24.0, 36.8],    // Berenice 35.5 west, Yanbu 38.06 east
    [27.5, 34.0],    // Hurghada 33.81 west, Ras Muhammad 34.25 east
    [28.3, 33.35],   // Gulf of Suez
    [29.95, 32.57],  // Suez
    [30.5, 32.34],   // Suez Canal
    [31.6, 32.34],
];

// [lat, lon]: the Bosphorus channel. Europe lies west of it; south of 41.0 N
// the value is the Sea of Marmara's north shore limit.
const BOSPHORUS = [
    [41.0, 29.005],  // Eminonu 28.97 west, Kadikoy 29.03 east
    [41.08, 29.06],
    [41.2, 29.12],
];

// [lon, lat]: the Greater Caucasus crest. Europe lies north of it.
const CAUCASUS_CREST = [
    [37.0, 43.0],
    [40.0, 43.0],    // Sochi 43.6 north
    [44.0, 42.6],    // Grozny 43.3 north, Tbilisi 41.7 south
    [46.5, 41.9],
    [49.6, 41.3],    // Derbent 42.06 north, Baku 40.4 south
    [50.5, 41.0],
];

function _isAfrica(lat, lon) {
    if (lon < -26 || lon > 52 || lat < -37) return false;
    if (lat >= _interp(AFRICA_NORTH_EDGE, lon)) return false;
    // South of Bab-el-Mandeb the Horn of Africa and Madagascar reach 51.4 E.
    return lat < AFRICA_EAST_EDGE[0][0] || lon < _interp(AFRICA_EAST_EDGE, lat);
}

// Called only after _isAfrica() returned false.
function _isEurope(lat, lon) {
    if (lon < -25 || lon > 60 || lat < 34 || lat > 82) return false;
    if (lon < 26) return true;
    if (lat < 40.0) {
        // Aegean: Dodecanese (Rhodes 28.2) south of 36.5 N, then the eastern
        // islands (Kos, Samos, Chios, Lesbos) against the Turkish coast.
        if (lat < 36.5) return lon < 28.3;
        if (lat < 37.5) return lon < 27.0;
        return lon < 26.6;
    }
    if (lat < 42.3) {
        if (lon >= 37) return lat > _interp(CAUCASUS_CREST, lon);
        // Thrace: north of the Sea of Marmara and west of the Bosphorus
        // (~29.0 E at Istanbul); the Gallipoli peninsula west of 26.7 E.
        return lon < _interp(BOSPHORUS, lat) && (lat >= 40.6 || lon < 26.7);
    }
    if (lat < 44 && lon >= 37) return lat > _interp(CAUCASUS_CREST, lon);
    if (lat < 50) return lon < 50;   // north of the Caspian
    return true;                     // Urals: lon <= 60 already checked
}

/**
 * Heuristic continent name for a point.
 * @param {number} lat - Latitude in degrees
 * @param {number} lon - Longitude in degrees
 * @returns {'Antarctica'|'Oceania'|'South America'|'North America'|'Africa'|'Europe'|'Asia'|'Other'}
 */
export function detectContinent(lat, lon) {
    if (lat < -60) return 'Antarctica';
    if (lat >= -55 && lat <= -10 && lon >= 110 && lon <= 180) return 'Oceania';
    if (lat >= -10 && lat <= 0 && lon >= 130 && lon <= 180) return 'Oceania';
    if (lat >= -56 && lat <= 13 && lon >= -82 && lon <= -34) return 'South America';
    if (lat >= 13 && lat <= 75 && lon >= -168 && lon <= -52) return 'North America';
    if (lat >= 8 && lat <= 28 && lon >= -90 && lon <= -52) return 'North America';
    if (_isAfrica(lat, lon)) return 'Africa';
    if (_isEurope(lat, lon)) return 'Europe';
    // Everything else east of the Bosphorus / Red Sea: Anatolia, the Levant,
    // Arabia, the Caucasus south of the crest, and the Asian mainland and
    // islands (the western Pacific stops at 150 E south of 40 N).
    if (lat <= 82 && lon <= 180 && ((lat >= 12 && lon >= 26) || (lat >= -11 && lon >= 60))) {
        if (lat < 40 && lon > 150) return 'Other';
        return 'Asia';
    }
    return 'Other';
}
