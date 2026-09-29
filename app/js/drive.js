// "Just drive" mode: no tour, no destination. The engine divides the world into ~11 km
// map cells, each with its own small story pack. As you drive we fetch the cells ahead of you,
// save them for offline use, and hand their stops to the trigger engine.

import { destination } from './geo.js';
import { downloadPack, loadManifest } from './packs.js';

export const CELL_DEG = 0.1;
const INDEX_KEY = 'cairn:cells';
const RETRY_MS = 3000;
const MAX_BUILD_WAIT_MS = 5 * 60 * 1000;
const MAX_IN_FLIGHT = 2;

export const cellId = (lat, lon) => `${Math.floor(lat / CELL_DEG + 1e-9)}_${Math.floor(lon / CELL_DEG + 1e-9)}`;

/**
 * Cells worth having for this fix, nearest first: where you are, then points ahead along your
 * heading (plus a little to each side). When stopped or heading is unknown: the 3×3 around you.
 */
export function cellsAhead(fix, { ahead = [4000, 8000, 12000, 16000], side = 2500 } = {}) {
  const ids = [cellId(fix.lat, fix.lon)];
  const add = (p) => { const id = cellId(p.lat, p.lon); if (!ids.includes(id)) ids.push(id); };
  if (fix.heading == null || Number.isNaN(fix.heading) || (fix.speed ?? 0) < 3) {
    for (const dLat of [-1, 0, 1]) for (const dLon of [-1, 0, 1]) add({ lat: fix.lat + dLat * CELL_DEG, lon: fix.lon + dLon * CELL_DEG });
    return ids;
  }
  for (const d of ahead) {
    const p = destination(fix, fix.heading, d);
    add(p);
    add(destination(p, fix.heading - 90, side));
    add(destination(p, fix.heading + 90, side));
  }
  return ids;
}

function loadIndex() {
  try { return JSON.parse(localStorage.getItem(INDEX_KEY)) ?? {}; } catch { return {}; }
}

export class DriveMode {
  /**
   * @param {object} opts
   * @param {string|null} opts.engineBase  engine server URL (null when offline / none)
   * @param {string} [opts.source]           e.g. 'fixtures' for demo data
   * @param {(manifest: object, href: string) => void} opts.onCell  a cell's stops are ready
   */
  constructor({ engineBase, source, onCell, onChange = () => {} }) {
    this.base = engineBase ? engineBase.replace(/\/?$/, '/') : null;
    this.source = source;
    this.onCell = onCell;
    this.onChange = onChange;
    this.index = loadIndex(); // cellId -> { href } | { empty: true }
    this.state = new Map(); // cellId -> 'loading' | 'ready' | 'empty' | 'waiting' | 'failed'
    this.failedAt = new Map();
    this.inFlight = 0;
    this.pending = [];
    this.stopped = false;
  }

  get stats() {
    const v = [...this.state.values()];
    return { ready: v.filter((s) => s === 'ready').length, loading: v.filter((s) => s === 'loading' || s === 'waiting').length };
  }

  stop() {
    this.stopped = true;
  }

  update(fix) {
    for (const id of cellsAhead(fix)) {
      const st = this.state.get(id);
      // Failed cells (no signal, server busy) are retried after a minute.
      if (st && !(st === 'failed' && Date.now() - (this.failedAt.get(id) ?? 0) > 60_000)) continue;
      this.state.set(id, 'waiting');
      this.pending.push(id);
    }
    this.pump();
  }

  pump() {
    while (!this.stopped && this.inFlight < MAX_IN_FLIGHT && this.pending.length) {
      const id = this.pending.shift();
      this.inFlight++;
      this.load(id).finally(() => {
        if (this.state.get(id) === 'failed') this.failedAt.set(id, Date.now());
        this.inFlight--;
        this.onChange();
        this.pump();
      });
    }
  }

  save(id, value) {
    this.index[id] = value;
    try { localStorage.setItem(INDEX_KEY, JSON.stringify(this.index)); } catch { /* storage full or blocked */ }
  }

  async load(id) {
    this.state.set(id, 'loading');
    const known = this.index[id];
    if (known?.empty) return this.state.set(id, 'empty');
    if (known?.href) {
      // Downloaded on an earlier drive: works with no signal.
      try {
        const { manifest, href } = await loadManifest({ id: `cell-${id}`, manifest: known.href, base: known.href });
        this.state.set(id, 'ready');
        this.onCell(manifest, href);
        return;
      } catch { /* fall through and refetch */ }
    }
    if (!this.base || !navigator.onLine) return this.state.set(id, 'failed');

    const started = Date.now();
    const q = this.source ? `?source=${encodeURIComponent(this.source)}` : '';
    while (!this.stopped && Date.now() - started < MAX_BUILD_WAIT_MS) {
      let res;
      try {
        res = await fetch(`${this.base}v1/cells/${id}${q}`, { cache: 'no-store' });
      } catch {
        return this.state.set(id, 'failed');
      }
      const body = await res.json().catch(() => ({}));
      if (body.status === 'empty') {
        this.save(id, { empty: true });
        return this.state.set(id, 'empty');
      }
      if (body.status === 'ready') {
        const entry = { id: `cell-${id}`, name: id, manifest: body.manifest, base: this.base };
        try {
          await downloadPack(entry, () => {}, { remember: false });
          const { manifest, href } = await loadManifest(entry);
          this.save(id, { href });
          this.state.set(id, 'ready');
          this.onCell(manifest, href);
        } catch {
          this.state.set(id, 'failed');
        }
        return;
      }
      if (!res.ok && res.status !== 202) return this.state.set(id, 'failed');
      await new Promise((r) => setTimeout(r, RETRY_MS)); // still being built
    }
    this.state.set(id, 'failed');
  }
}
