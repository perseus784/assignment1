// Small, dependency-free geodesy helpers. All angles in degrees, distances in meters.

const EARTH_RADIUS = 6371008.8;

export const toRad = (deg) => (deg * Math.PI) / 180;
export const toDeg = (rad) => (rad * 180) / Math.PI;

/** Great-circle distance between two {lat, lon} points (haversine). */
export function distance(a, b) {
  const dLat = toRad(b.lat - a.lat);
  const dLon = toRad(b.lon - a.lon);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(a.lat)) * Math.cos(toRad(b.lat)) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_RADIUS * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Initial bearing from a to b, 0..360 (0 = north, 90 = east). */
export function bearing(a, b) {
  const φ1 = toRad(a.lat);
  const φ2 = toRad(b.lat);
  const Δλ = toRad(b.lon - a.lon);
  const y = Math.sin(Δλ) * Math.cos(φ2);
  const x = Math.cos(φ1) * Math.sin(φ2) - Math.sin(φ1) * Math.cos(φ2) * Math.cos(Δλ);
  return (toDeg(Math.atan2(y, x)) + 360) % 360;
}

/** Smallest absolute difference between two headings, 0..180. */
export function angleDiff(a, b) {
  const d = Math.abs(((a - b) % 360) + 360) % 360;
  return d > 180 ? 360 - d : d;
}

/** Point reached by travelling `dist` meters from `p` on initial bearing `brg`. */
export function destination(p, brg, dist) {
  const δ = dist / EARTH_RADIUS;
  const θ = toRad(brg);
  const φ1 = toRad(p.lat);
  const λ1 = toRad(p.lon);
  const φ2 = Math.asin(Math.sin(φ1) * Math.cos(δ) + Math.cos(φ1) * Math.sin(δ) * Math.cos(θ));
  const λ2 = λ1 + Math.atan2(Math.sin(θ) * Math.sin(δ) * Math.cos(φ1), Math.cos(δ) - Math.sin(φ1) * Math.sin(φ2));
  return { lat: toDeg(φ2), lon: ((toDeg(λ2) + 540) % 360) - 180 };
}

/** Total length of a polyline of {lat, lon} points. */
export function routeLength(points) {
  let total = 0;
  for (let i = 1; i < points.length; i++) total += distance(points[i - 1], points[i]);
  return total;
}

/**
 * Position `dist` meters along a polyline, with the heading of the segment it lies on.
 * Clamps to the ends of the route.
 */
export function pointAlongRoute(points, dist) {
  if (points.length === 1) return { ...points[0], heading: 0 };
  let remaining = Math.max(0, dist);
  for (let i = 1; i < points.length; i++) {
    const a = points[i - 1];
    const b = points[i];
    const seg = distance(a, b);
    if (remaining <= seg || i === points.length - 1) {
      const t = seg === 0 ? 0 : Math.min(1, remaining / seg);
      return {
        lat: a.lat + (b.lat - a.lat) * t,
        lon: a.lon + (b.lon - a.lon) * t,
        heading: bearing(a, b),
      };
    }
    remaining -= seg;
  }
  // unreachable, loop always returns on the last segment
  return null;
}

/** True if a point lies inside a {north, south, east, west} bounding box. */
export function inBounds(p, bounds) {
  return p.lat <= bounds.north && p.lat >= bounds.south && p.lon <= bounds.east && p.lon >= bounds.west;
}

export function formatDistance(meters, units = 'imperial') {
  if (units === 'metric') {
    return meters < 1000 ? `${Math.round(meters / 10) * 10} m` : `${(meters / 1000).toFixed(1)} km`;
  }
  const feet = meters * 3.28084;
  return feet < 1000 ? `${Math.round(feet / 50) * 50} ft` : `${(meters / 1609.344).toFixed(1)} mi`;
}
