// Geometry: the camera maths shared with the tests (the pinhole-math block), metre offsets, the pair-name lookup.
import { st } from "./state.js";
// <pinhole-math>
// Camera maths shared by the page and tests/test_photo_label_register.py (run under node).
// pinholeProject == city2stl.skyline.photo_view.PinholeCamera.project_ll; cesiumView/cesiumBasis map
// a PinholeCamera onto Cesium's heading/pitch/roll/fov and back, so a render matches the photo's pixels.

const M_PER_DEG_LAT = 111320;
function pinholeAxes(c) {                 // [right, up, fwd] in east, north, up
  const h = c.heading_deg * Math.PI / 180, p = c.pitch_deg * Math.PI / 180, r = (c.roll_deg || 0) * Math.PI / 180;
  const fwd = [Math.sin(h) * Math.cos(p), Math.cos(h) * Math.cos(p), Math.sin(p)];
  const r0 = [Math.cos(h), -Math.sin(h), 0];
  const u0 = [r0[1] * fwd[2] - r0[2] * fwd[1], r0[2] * fwd[0] - r0[0] * fwd[2], r0[0] * fwd[1] - r0[1] * fwd[0]];
  const right = r0.map((v, i) => Math.cos(r) * v + Math.sin(r) * u0[i]);
  const up = r0.map((v, i) => -Math.sin(r) * v + Math.cos(r) * u0[i]);
  return [right, up, fwd];
}
function pinholeProject(c, lat, lon, h) {
  const kx = M_PER_DEG_LAT * Math.cos(c.lat * Math.PI / 180);
  const d = [(lon - c.lon) * kx, (lat - c.lat) * M_PER_DEG_LAT, h - c.elev_m];
  const [right, up, fwd] = pinholeAxes(c), dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const z = dot(d, fwd);
  if (z <= 1e-9) return { x: NaN, y: NaN, z };
  return { x: c.width / 2 + c.f_px * dot(d, right) / z, y: c.height / 2 - c.f_px * dot(d, up) / z, z };
}
// Cesium orientation of a pinhole camera drawn at rw x rh pixels (rw/cam.width scales f).
// Cesium roll is the opposite sign of the pinhole roll; frustum.fov is the horizontal angle when aspect >= 1.
function cesiumView(c, rw, rh) {
  const f = c.f_px * rw / c.width, fovy = 2 * Math.atan(rh / 2 / f), hfov = 2 * Math.atan(rw / 2 / f);
  return { heading: c.heading_deg * Math.PI / 180, pitch: c.pitch_deg * Math.PI / 180, roll: -(c.roll_deg || 0) * Math.PI / 180,
           fovy, hfov, fov: rw / rh >= 1 ? hfov : fovy, aspect: rw / rh };
}
// Cesium's camera basis in east, north, up for a heading/pitch/roll (Camera.setView3D): the orientation
// quaternion is Rz(-(heading - 90 deg)) Ry(-pitch) Rx(roll); direction is its first column, up its third.
function cesiumBasis(heading, pitch, roll) {
  const mul = (a, b) => a.map((_, i) => [0, 1, 2].map(j => a[i][0] * b[0][j] + a[i][1] * b[1][j] + a[i][2] * b[2][j]));
  const rx = a => [[1, 0, 0], [0, Math.cos(a), -Math.sin(a)], [0, Math.sin(a), Math.cos(a)]];
  const ry = a => [[Math.cos(a), 0, Math.sin(a)], [0, 1, 0], [-Math.sin(a), 0, Math.cos(a)]];
  const rz = a => [[Math.cos(a), -Math.sin(a), 0], [Math.sin(a), Math.cos(a), 0], [0, 0, 1]];
  const m = mul(mul(rz(-(heading - Math.PI / 2)), ry(-pitch)), rx(roll));
  const fwd = [m[0][0], m[1][0], m[2][0]], up = [m[0][2], m[1][2], m[2][2]];
  const right = [fwd[1] * up[2] - fwd[2] * up[1], fwd[2] * up[0] - fwd[0] * up[2], fwd[0] * up[1] - fwd[1] * up[0]];
  return { right, up, fwd };
}
// </pinhole-math>
export { M_PER_DEG_LAT, pinholeAxes, pinholeProject, cesiumView, cesiumBasis };

// East / north metres of (lat, lon) from (lat0, lon0), and the inverse: [lat, lon] at e / n metres from the origin.
export function enuOffset(lat0, lon0, lat, lon) {
  return { e: (lon - lon0) * M_PER_DEG_LAT * Math.cos(lat0 * Math.PI / 180), n: (lat - lat0) * M_PER_DEG_LAT };
}
export function offsetLL(lat0, lon0, e, n) {
  return [lat0 + n / M_PER_DEG_LAT, lon0 + e / (M_PER_DEG_LAT * Math.cos(lat0 * Math.PI / 180))];
}

export function nameAt(lat, lon) {
  if (!st.footprints) return null;
  for (const f of st.footprints.features) {
    const nm = f.properties.name; if (!nm) continue;
    const ring = f.geometry.coordinates[0]; let c = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i], [xj, yj] = ring[j];
      if ((yi > lat) !== (yj > lat) && lon < (xj - xi) * (lat - yi) / (yj - yi) + xi) c = !c;
    }
    if (c) return nm;
  }
  return null;
}
