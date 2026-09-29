// Map view (MapLibre GL, vendored so it works offline).
// Basemap: a pack's own offline PMTiles file if it has one, else OpenFreeMap vector tiles
// when online (tiles you've seen are cached by the service worker), else a plain canvas.
// Route, stops and your position are always drawn from the pack, so they work offline.

import * as maplibregl from '../vendor/maplibre/maplibre-gl.mjs';

const ONLINE_STYLE = 'https://tiles.openfreemap.org/styles/liberty';

const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function offlineStyle() {
  return { version: 8, sources: {}, layers: [{ id: 'bg', type: 'background', paint: { 'background-color': css('--map-bg') } }] };
}

// Minimal Protomaps-schema style without text (no font downloads needed offline).
function pmtilesStyle(url) {
  const land = css('--map-bg');
  return {
    version: 8,
    sources: { base: { type: 'vector', url: `pmtiles://${url}`, attribution: '© OpenStreetMap contributors' } },
    layers: [
      { id: 'bg', type: 'background', paint: { 'background-color': land } },
      { id: 'earth', type: 'fill', source: 'base', 'source-layer': 'earth', paint: { 'fill-color': land } },
      { id: 'green', type: 'fill', source: 'base', 'source-layer': 'landuse', filter: ['in', ['get', 'kind'], ['literal', ['park', 'national_park', 'nature_reserve', 'forest', 'wood', 'protected_area']]], paint: { 'fill-color': css('--map-green'), 'fill-opacity': 0.6 } },
      { id: 'water', type: 'fill', source: 'base', 'source-layer': 'water', paint: { 'fill-color': css('--map-water') } },
      { id: 'roads-minor', type: 'line', source: 'base', 'source-layer': 'roads', filter: ['in', ['get', 'kind'], ['literal', ['minor_road', 'path', 'other']]], paint: { 'line-color': css('--map-road'), 'line-width': 1 } },
      { id: 'roads-major', type: 'line', source: 'base', 'source-layer': 'roads', filter: ['in', ['get', 'kind'], ['literal', ['highway', 'major_road', 'medium_road']]], paint: { 'line-color': css('--map-road'), 'line-width': 2.2 } },
    ],
  };
}

let pmtilesRegistered = false;

export class TourMap {
  constructor(container, { onStopClick } = {}) {
    this.container = container;
    this.onStopClick = onStopClick ?? (() => {});
    this.markers = new Map();
    this.you = null;
    this.follow = true;
    this.map = null;
    this.manifest = null;
  }

  async show(manifest, assetUrl) {
    this.manifest = manifest;
    const style = await this.pickStyle(manifest, assetUrl);
    if (!this.map) {
      this.map = new maplibregl.Map({
        container: this.container,
        style,
        bounds: toBounds(manifest.bounds),
        fitBoundsOptions: { padding: 30 },
        attributionControl: { compact: true },
        cooperativeGestures: false,
        pitchWithRotate: false,
      });
      this.map.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: false }), 'top-right');
      this.map.on('dragstart', () => { this.follow = false; });
      this.map.on('error', (e) => {
        // Tiles unavailable (offline, blocked): fall back to the plain canvas once.
        if (!this.fellBack && /tiles|style|Failed to fetch|NetworkError/i.test(String(e?.error?.message ?? e?.error ?? ''))) {
          this.fellBack = true;
          this.map.setStyle(offlineStyle());
          this.map.once('styledata', () => this.drawOverlays());
        }
      });
    } else {
      this.map.setStyle(style);
      this.map.fitBounds(toBounds(manifest.bounds), { padding: 30, duration: 0 });
    }
    await new Promise((resolve) => (this.map.isStyleLoaded() ? resolve() : this.map.once('load', resolve)));
    this.drawOverlays();
  }

  async pickStyle(manifest, assetUrl) {
    if (manifest.map?.pmtiles && window.pmtiles) {
      if (!pmtilesRegistered) {
        maplibregl.addProtocol('pmtiles', new window.pmtiles.Protocol().tile);
        pmtilesRegistered = true;
      }
      return pmtilesStyle(assetUrl(manifest.map.pmtiles));
    }
    if (!navigator.onLine) return offlineStyle();
    // Only use the online basemap if it's actually reachable right now.
    try {
      const res = await fetch(ONLINE_STYLE, { signal: AbortSignal.timeout(4000) });
      if (res.ok) return await res.json();
    } catch { /* unreachable: fall through */ }
    return offlineStyle();
  }

  drawOverlays() {
    const m = this.manifest;
    if (!m || !this.map) return;
    const route = m.route?.length ? m.route.map(([lat, lon]) => [lon, lat]) : m.free ? [] : m.stops.map((s) => [s.place.lon, s.place.lat]);
    const data = route.length > 1
      ? { type: 'Feature', geometry: { type: 'LineString', coordinates: route } }
      : { type: 'FeatureCollection', features: [] };
    if (this.map.getSource('route')) {
      this.map.getSource('route').setData(data);
    } else {
      this.map.addSource('route', { type: 'geojson', data });
      this.map.addLayer({ id: 'route-casing', type: 'line', source: 'route', layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': css('--route-casing'), 'line-width': 8 } });
      this.map.addLayer({ id: 'route', type: 'line', source: 'route', layout: { 'line-join': 'round', 'line-cap': 'round' }, paint: { 'line-color': css('--accent'), 'line-width': 4, ...(m.route ? {} : { 'line-dasharray': [1.5, 1.5] }) } });
    }
    for (const mk of this.markers.values()) mk.remove();
    this.markers.clear();
    m.stops.forEach((s, i) => this.addMarker(s, i));
  }

  addMarker(s, i) {
    const el = document.createElement('button');
    el.className = this.manifest.free ? 'stop-marker free' : 'stop-marker';
    el.textContent = this.manifest.free ? '' : String(i + 1);
    el.title = s.name;
    el.setAttribute('aria-label', this.manifest.free ? s.name : `Stop ${i + 1}: ${s.name}`);
    el.addEventListener('click', (e) => { e.stopPropagation(); this.onStopClick(s); });
    this.markers.set(s.id, new maplibregl.Marker({ element: el }).setLngLat([s.place.lon, s.place.lat]).addTo(this.map));
  }

  /** "Just drive" mode: an open-ended map around the driver that gains stops as cells arrive. */
  async showFree(center) {
    const d = 0.08;
    await this.show({ free: true, route: null, stops: [], bounds: { south: center.lat - d, north: center.lat + d, west: center.lon - d, east: center.lon + d } }, (p) => p);
    this.follow = true;
  }

  addStops(stops) {
    if (!this.manifest?.free || !this.map) return;
    for (const s of stops) {
      if (this.markers.has(s.id)) continue;
      this.manifest.stops.push(s);
      this.addMarker(s, this.manifest.stops.length - 1);
    }
  }

  setPlayed(played, currentId) {
    for (const [id, mk] of this.markers) {
      mk.getElement().classList.toggle('played', played.has(id) && id !== currentId);
      mk.getElement().classList.toggle('current', id === currentId);
    }
  }

  setPosition(fix) {
    if (!this.map) return;
    if (!this.you) {
      const el = document.createElement('div');
      el.className = 'you-marker';
      el.innerHTML = '<svg viewBox="0 0 24 24" width="30" height="30" aria-hidden="true"><path d="M12 2 20 21 12 16.5 4 21Z"/></svg>';
      this.you = new maplibregl.Marker({ element: el, rotationAlignment: 'map' }).setLngLat([fix.lon, fix.lat]).addTo(this.map);
    }
    this.you.setLngLat([fix.lon, fix.lat]);
    this.you.setRotation(fix.heading ?? 0);
    if (this.follow) this.map.easeTo({ center: [fix.lon, fix.lat], zoom: Math.max(this.map.getZoom(), 12.5), duration: 800 });
  }

  recenter() {
    this.follow = true;
    if (this.you) this.map.easeTo({ center: this.you.getLngLat(), zoom: 13.5, duration: 600 });
  }

  resize() {
    this.map?.resize();
  }
}

function toBounds(b) {
  return [[b.west, b.south], [b.east, b.north]];
}
