// Pure helpers that turn a tour manifest into what the app needs. No DOM, unit-tested.

import { distance } from './geo.js';

/** The shape TourEngine expects (geofences keyed by stop id). */
export function toEnginePack(manifest) {
  return {
    id: manifest.id,
    name: manifest.name,
    bounds: manifest.bounds,
    intro: manifest.intro ? { title: manifest.intro.title, story: manifest.intro.transcript ?? '' } : null,
    pois: manifest.stops.map((s) => ({
      id: s.id,
      name: s.name,
      category: s.category,
      lat: s.trigger.lat,
      lon: s.trigger.lon,
      radius: s.trigger.radius,
      priority: s.priority ?? 1,
      reach: s.trigger.reach ?? 0,
      story: s.episode.transcript ?? '',
    })),
    ambient: (manifest.ambient ?? []).map((a) => ({ id: a.id, title: a.title, story: a.episode.transcript ?? '' })),
  };
}

/** Episode for an id the engine triggered ('__intro', a stop id or an ambient id). */
export function episodeFor(manifest, id) {
  if (id === '__intro') return manifest.intro ? { id, title: manifest.intro.title, kind: 'intro', episode: manifest.intro } : null;
  const stop = manifest.stops.find((s) => s.id === id);
  if (stop) return { id, title: stop.name, kind: 'stop', stop, episode: stop.episode };
  const amb = (manifest.ambient ?? []).find((a) => a.id === id);
  if (amb) return { id, title: amb.title, kind: 'ambient', episode: amb.episode };
  return null;
}

/** Next stop in tour order that hasn't been heard, and how far away it is. */
export function nextStop(manifest, played, fix) {
  const remaining = manifest.stops.filter((s) => !played.has(s.id)).sort((a, b) => a.order - b.order);
  if (!remaining.length) return null;
  const stop = remaining[0];
  return { stop, distance: fix ? distance(fix, stop.place) : null };
}

/** Deep link into the driver's navigation app. */
export function navigationUrl(place, app = 'auto', ua = typeof navigator !== 'undefined' ? navigator.userAgent : '') {
  const { lat, lon } = place;
  const chosen = app === 'auto' ? (/iPhone|iPad|Macintosh/.test(ua) ? 'apple' : 'google') : app;
  if (chosen === 'apple') return `https://maps.apple.com/?daddr=${lat},${lon}&dirflg=d`;
  if (chosen === 'waze') return `https://waze.com/ul?ll=${lat},${lon}&navigate=yes`;
  return `https://www.google.com/maps/dir/?api=1&destination=${lat},${lon}&travelmode=driving`;
}

export const CATEGORY_LABEL = {
  geology: 'Geology', water: 'Water', nature: 'Nature', railway: 'Railway', mining: 'Mining', roads: 'Roads',
  town: 'Town', history: 'History', religion: 'Faith', culture: 'Culture', landmark: 'Landmark',
};

export function formatBytes(n) {
  if (!n) return '';
  return n > 1e6 ? `${(n / 1e6).toFixed(1)} MB` : `${Math.round(n / 1e3)} KB`;
}
