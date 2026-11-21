/**
 * explore/globeMath.ts - the globe's geometry, kept pure so it can be tested without WebGL.
 *
 * The sphere is THREE.SphereGeometry textured with an equirectangular image (static/global_dem.png,
 * longitude -180 at the left edge). For that texture a point at (lat, lng) sits at
 *   x = r cos(lat) cos(lng),  y = r sin(lat),  z = -r cos(lat) sin(lng)
 * (the old marker formula had x mirrored, which put every marker on the wrong side of a textured
 * globe). Rotating the globe group about Y by `yawToFace(lng)` brings longitude `lng` to the
 * camera, which looks down -z from +z.
 */
const RAD = Math.PI / 180;

export interface Vec3 { x: number; y: number; z: number }

export function latLngToVec(lat: number, lng: number, radius: number): Vec3 {
    const c = Math.cos(lat * RAD);
    return {
        x: radius * c * Math.cos(lng * RAD),
        y: radius * Math.sin(lat * RAD),
        z: -radius * c * Math.sin(lng * RAD),
    };
}

/** Rotation about Y (radians) that turns longitude `lng` toward the camera. */
export function yawToFace(lng: number): number {
    return (-90 - lng) * RAD;
}

/** Rotate a point about X then Y: THREE's Euler order 'YXZ' (GlobeView sets it on the globe group). */
export function rotateXY(v: Vec3, rx: number, ry: number): Vec3 {
    const cx = Math.cos(rx), sx = Math.sin(rx);
    const y1 = v.y * cx - v.z * sx;
    const z1 = v.y * sx + v.z * cx;
    const cy = Math.cos(ry), sy = Math.sin(ry);
    return { x: v.x * cy + z1 * sy, y: y1, z: -v.x * sy + z1 * cy };
}

/** True when a marker (already rotated into the camera frame) is on the visible hemisphere. */
export function facesCamera(world: Vec3, cameraZ: number, radius: number): boolean {
    // Visible when the point is nearer the camera than the sphere's horizon plane.
    return world.z > (radius * radius) / cameraZ;
}

export function clamp(v: number, lo: number, hi: number): number {
    return Math.min(hi, Math.max(lo, v));
}
