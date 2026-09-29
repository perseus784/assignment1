import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { TourEngine, enrichFix } from '../js/engine.js';
import { destination, pointAlongRoute, routeLength } from '../js/geo.js';
import { validatePack } from '../js/packs.js';

const origin = { lat: 44.5, lon: -110.8 };
const poiNorth = { id: 'north', name: 'North', ...destination(origin, 0, 1000), radius: 200, story: 'N' };
const poiEast = { id: 'east', name: 'East', ...destination(origin, 90, 1000), radius: 200, story: 'E' };
const pack = { id: 't', name: 'Test', pois: [poiNorth, poiEast] };
const fix = (p, extra = {}) => ({ lat: p.lat, lon: p.lon, timestamp: 0, speed: 0, heading: null, ...extra });

test('a parked visitor only triggers inside the geofence', () => {
  const engine = new TourEngine(pack);
  assert.equal(engine.update(fix(origin)).triggered.length, 0);
  const inside = destination(origin, 0, 850);
  const r = engine.update(fix(inside, { timestamp: 1000 }));
  assert.deepEqual(r.triggered.map((t) => t.id), ['north']);
});

test('a driver gets the story early when the place is ahead', () => {
  const engine = new TourEngine(pack, { leadSeconds: 20 });
  // 500 m before the fence edge, driving north at 20 m/s → 400 m lead is not quite enough…
  const p1 = destination(origin, 0, 300);
  assert.equal(engine.update(fix(p1, { speed: 20, heading: 0 })).triggered.length, 0);
  // …but 350 m before the edge it is.
  const p2 = destination(origin, 0, 450);
  assert.deepEqual(engine.update(fix(p2, { speed: 20, heading: 0, timestamp: 1 })).triggered.map((t) => t.id), ['north']);
});

test('places behind you do not trigger early', () => {
  const engine = new TourEngine(pack, { leadSeconds: 20 });
  const p = destination(origin, 0, 450);
  // Same spot, but driving south (away from "north")
  assert.equal(engine.update(fix(p, { speed: 20, heading: 180 })).triggered.length, 0);
});

test('stories never repeat and the played set can be restored', () => {
  const inside = destination(origin, 0, 1000);
  const engine = new TourEngine(pack);
  assert.equal(engine.update(fix(inside)).triggered.length, 1);
  assert.equal(engine.update(fix(inside, { timestamp: 5 })).triggered.length, 0);
  const restored = new TourEngine(pack, {}, engine.played);
  assert.equal(restored.update(fix(inside)).triggered.length, 0);
  restored.reset();
  assert.equal(restored.update(fix(inside)).triggered.length, 1);
});

test('higher priority plays first when several trigger at once', () => {
  const a = { id: 'a', name: 'A', ...origin, radius: 100, priority: 1, story: 'a' };
  const b = { id: 'b', name: 'B', ...origin, radius: 100, priority: 3, story: 'b' };
  const engine = new TourEngine({ id: 'x', name: 'X', pois: [a, b] });
  assert.deepEqual(engine.update(fix(origin)).triggered.map((t) => t.id), ['b', 'a']);
});

test('intro plays on entering the park, ambient facts fill long quiet stretches', () => {
  const p = {
    ...pack,
    bounds: { north: 45, south: 44, east: -110, west: -111 },
    intro: { story: 'hello' },
    ambient: [{ id: 'f1', title: 'Fact', story: 'fact' }],
  };
  const engine = new TourEngine(p, { ambientAfterMs: 60_000 });
  assert.deepEqual(engine.update(fix({ lat: 46, lon: -110.5 })).triggered, []); // outside
  assert.deepEqual(engine.update(fix(origin, { timestamp: 1000 })).triggered.map((t) => t.id), ['__intro']);
  assert.equal(engine.update(fix(origin, { timestamp: 30_000 })).triggered.length, 0);
  assert.deepEqual(engine.update(fix(origin, { timestamp: 62_000 })).triggered.map((t) => t.id), ['f1']);
  assert.equal(engine.update(fix(origin, { timestamp: 200_000 })).triggered.length, 0); // no facts left
});

test('enrichFix derives speed and heading from consecutive fixes', () => {
  const a = { ...origin, timestamp: 0 };
  const b = { ...destination(origin, 90, 100), timestamp: 10_000, speed: null, heading: null };
  const e = enrichFix(b, a);
  assert.ok(Math.abs(e.speed - 10) < 0.1);
  assert.ok(Math.abs(e.heading - 90) < 1);
});

test('sample packs are valid and the demo drive hears every roadside story in order', () => {
  const index = JSON.parse(readFileSync(new URL('../packs/index.json', import.meta.url)));
  for (const entry of index.packs) {
    const p = JSON.parse(readFileSync(new URL(`../${entry.url}`, import.meta.url)));
    assert.equal(p.id, entry.id);
    validatePack(p);

    const engine = new TourEngine(p, { ambientAfterMs: Infinity });
    const heard = [];
    const len = routeLength(p.demoRoute);
    for (let d = 0, t = 0; d <= len + 15; d += 15, t += 1000) {
      const pt = pointAlongRoute(p.demoRoute, d);
      heard.push(...engine.update({ lat: pt.lat, lon: pt.lon, heading: pt.heading, speed: 15, timestamp: t }).triggered.map((x) => x.id));
    }
    const missed = p.pois.filter((poi) => !heard.includes(poi.id)).map((poi) => poi.id);
    assert.deepEqual(missed, [], `${p.id}: demo route never triggers ${missed.join(', ')}`);
    assert.equal(heard[0], '__intro');
  }
});
