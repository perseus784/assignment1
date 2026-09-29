import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { toEnginePack, episodeFor, nextStop, navigationUrl, formatBytes } from '../js/tour.js';
import { rankVoices } from '../js/speech.js';

const manifest = JSON.parse(readFileSync(new URL('../packs/million-dollar-highway/manifest.json', import.meta.url)));

test('toEnginePack uses road-side triggers, not the place itself', () => {
  const pack = toEnginePack(manifest);
  assert.equal(pack.pois.length, manifest.stops.length);
  const s = manifest.stops[0];
  assert.deepEqual([pack.pois[0].lat, pack.pois[0].lon, pack.pois[0].radius], [s.trigger.lat, s.trigger.lon, s.trigger.radius]);
  assert.ok(pack.intro && pack.bounds);
});

test('episodeFor resolves intro, stops and unknown ids', () => {
  assert.equal(episodeFor(manifest, '__intro').kind, 'intro');
  const id = manifest.stops[2].id;
  assert.equal(episodeFor(manifest, id).stop.id, id);
  assert.equal(episodeFor(manifest, 'nope'), null);
});

test('nextStop skips heard stops and reports distance', () => {
  const first = manifest.stops[0];
  const n = nextStop(manifest, new Set([first.id]), first.place);
  assert.equal(n.stop.id, manifest.stops[1].id);
  assert.ok(n.distance > 0);
  assert.equal(nextStop(manifest, new Set(manifest.stops.map((s) => s.id)), null), null);
});

test('navigationUrl picks an app per platform', () => {
  const p = { lat: 37.9, lon: -107.7 };
  assert.match(navigationUrl(p, 'auto', 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0)'), /^https:\/\/maps\.apple\.com\/\?daddr=37\.9,-107\.7/);
  assert.match(navigationUrl(p, 'auto', 'Mozilla/5.0 (Linux; Android 14)'), /google\.com\/maps\/dir\/\?api=1&destination=37\.9,-107\.7/);
  assert.match(navigationUrl(p, 'waze'), /waze\.com\/ul\?ll=37\.9,-107\.7&navigate=yes/);
});

test('rankVoices prefers on-device, higher-quality English voices', () => {
  const voices = [
    { name: 'Remote English', lang: 'en-US', localService: false },
    { name: 'Deutsch', lang: 'de-DE', localService: true },
    { name: 'Local English', lang: 'en-GB', localService: true },
    { name: 'Ava (Enhanced)', lang: 'en-US', localService: true },
  ];
  assert.deepEqual(rankVoices(voices).map((v) => v.name), ['Ava (Enhanced)', 'Local English', 'Remote English']);
});

test('formatBytes', () => {
  assert.equal(formatBytes(2_676_672), '2.7 MB');
  assert.equal(formatBytes(512_000), '512 KB');
});
