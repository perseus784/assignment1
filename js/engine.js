// The tour engine decides *what* to narrate given a stream of GPS fixes.
// It is pure logic (no DOM, no timers) so it can be unit-tested with fake fixes.

import { distance, bearing, angleDiff, inBounds } from './geo.js';

export const DEFAULT_OPTIONS = {
  // When moving, start a story this many seconds before you reach the geofence edge…
  leadSeconds: 20,
  // …but never look further ahead than this.
  maxLeadMeters: 600,
  // A place counts as "ahead" if it is within this many degrees of your heading.
  coneDegrees: 50,
  // Below this speed (m/s) you're treated as parked/walking: only plain geofences fire.
  movingSpeed: 3,
  // After this long without a story while inside the park, play a general fact.
  ambientAfterMs: 4 * 60 * 1000,
  // How many upcoming places to report for the UI.
  nearbyCount: 6,
};

/**
 * @typedef {{lat:number, lon:number, speed?:number|null, heading?:number|null, timestamp:number}} Fix
 * @typedef {{id:string, name:string, lat:number, lon:number, radius:number, priority?:number, category?:string, story:string}} Poi
 */
export class TourEngine {
  /**
   * @param {object} pack  a tour pack (see packs/*.json)
   * @param {object} [options]
   * @param {Iterable<string>} [played] ids already narrated (restored from storage)
   */
  constructor(pack, options = {}, played = []) {
    this.pack = pack;
    this.opts = { ...DEFAULT_OPTIONS, ...options };
    this.played = new Set(played);
    this.lastNarrationAt = null;
    this.ambientIndex = 0;
  }

  reset() {
    this.played.clear();
    this.lastNarrationAt = null;
    this.ambientIndex = 0;
  }

  /** Mark something as narrated so it never fires again this trip. */
  markPlayed(id, timestamp) {
    this.played.add(id);
    if (timestamp != null) this.lastNarrationAt = timestamp;
  }

  /**
   * Feed a GPS fix. Returns the stories that should be queued now (highest priority first)
   * and a list of the closest unplayed places for display.
   * @param {Fix} fix
   */
  update(fix) {
    const { opts, pack } = this;
    const triggered = [];
    const insidePark = !pack.bounds || inBounds(fix, pack.bounds);
    if (this.lastNarrationAt == null) this.lastNarrationAt = fix.timestamp;

    if (insidePark && pack.intro && !this.played.has('__intro')) {
      triggered.push({ id: '__intro', name: pack.intro.title ?? `Welcome to ${pack.name}`, story: pack.intro.story, priority: 100 });
    }

    const moving = (fix.speed ?? 0) >= opts.movingSpeed && fix.heading != null && !Number.isNaN(fix.heading);
    const lead = moving ? Math.min(fix.speed * opts.leadSeconds, opts.maxLeadMeters) : 0;
    const nearby = [];

    for (const poi of pack.pois) {
      const d = distance(fix, poi);
      const brg = bearing(fix, poi);
      const played = this.played.has(poi.id);
      nearby.push({ poi, distance: d, bearing: brg, played });
      if (played) continue;

      const inside = d <= poi.radius;
      const approaching = moving && d <= poi.radius + lead && angleDiff(fix.heading, brg) <= opts.coneDegrees;
      if (inside || approaching) triggered.push({ ...poi, distance: d, priority: poi.priority ?? 1 });
    }

    triggered.sort((a, b) => (b.priority - a.priority) || ((a.distance ?? 0) - (b.distance ?? 0)));
    for (const t of triggered) this.markPlayed(t.id, fix.timestamp);

    // Nothing to say for a while? Share a general fact about the park.
    if (
      triggered.length === 0 &&
      insidePark &&
      pack.ambient?.length &&
      fix.timestamp - this.lastNarrationAt >= opts.ambientAfterMs
    ) {
      const fact = this.nextAmbient();
      if (fact) {
        triggered.push(fact);
        this.markPlayed(fact.id, fix.timestamp);
      }
    }

    nearby.sort((a, b) => a.distance - b.distance);
    return {
      triggered,
      insidePark,
      nearby: nearby.filter((n) => !n.played).slice(0, opts.nearbyCount),
      all: nearby,
    };
  }

  nextAmbient() {
    const list = this.pack.ambient;
    for (let i = 0; i < list.length; i++) {
      const fact = list[(this.ambientIndex + i) % list.length];
      if (!this.played.has(fact.id)) {
        this.ambientIndex = (this.ambientIndex + i + 1) % list.length;
        return { ...fact, name: fact.title, priority: 0, ambient: true };
      }
    }
    return null;
  }
}

/**
 * Fill in heading/speed when the GPS doesn't provide them (common on phones at low speed
 * or on desktop), by comparing with the previous fix.
 */
export function enrichFix(fix, prev) {
  const out = { ...fix };
  if (!prev) return out;
  const d = distance(prev, fix);
  const dt = (fix.timestamp - prev.timestamp) / 1000;
  if ((out.speed == null || Number.isNaN(out.speed)) && dt > 0) out.speed = d / dt;
  if ((out.heading == null || Number.isNaN(out.heading)) && d >= 5) out.heading = bearing(prev, fix);
  if ((out.heading == null || Number.isNaN(out.heading)) && prev.heading != null) out.heading = prev.heading;
  return out;
}
