import { test } from 'node:test';
import assert from 'node:assert/strict';
import { TourEngine, isPassing } from '../js/engine.js';
import { cellId, cellsAhead } from '../js/drive.js';
import { destination } from '../js/geo.js';

const LEWISVILLE = { lat: 33.046, lon: -96.994 };

test('cellId matches the engine grid (including negative coordinates)', () => {
  assert.equal(cellId(LEWISVILLE.lat, LEWISVILLE.lon), '330_-970');
  assert.equal(cellId(-33.87, 151.21), '-339_1512');
  assert.equal(cellId(44.4605, -110.8281), '444_-1109');
});

test('cellsAhead looks down the road when moving, all around when stopped', () => {
  const north = cellsAhead({ ...LEWISVILLE, heading: 0, speed: 30 });
  assert.equal(north[0], '330_-970');
  assert.ok(north.includes(cellId(destination(LEWISVILLE, 0, 16000).lat, LEWISVILLE.lon)));
  assert.ok(!north.includes(cellId(destination(LEWISVILLE, 180, 12000).lat, LEWISVILLE.lon)), 'nothing behind');
  const parked = cellsAhead({ ...LEWISVILLE, heading: null, speed: 0 });
  assert.equal(parked.length, 9);
});

test('isPassing: ahead and within reach of the path, never behind', () => {
  assert.ok(isPassing(1000, 60, 900, 2000)); // 500 m ahead, 866 m off the road
  assert.ok(!isPassing(1000, 120, 900, 2000)); // behind us
  assert.ok(!isPassing(3000, 10, 900, 2000)); // too far ahead yet
  assert.ok(!isPassing(2500, 80, 900, 2000)); // ahead-ish but 2.46 km off the road
});

test('a lake 1.5 km off the highway plays as you pass it, not after', () => {
  const lake = { id: 'lake', name: 'Lewisville Lake', ...destination(destination(LEWISVILLE, 0, 3000), 90, 1500), radius: 400, reach: 2500, story: 'x' };
  const engine = new TourEngine({ id: 'd', name: 'drive', pois: [] });
  engine.addPois([lake]);
  engine.addPois([lake]); // duplicates ignored
  assert.equal(engine.pack.pois.length, 1);
  const heard = [];
  for (let d = 0; d <= 6000; d += 100) {
    const p = destination(LEWISVILLE, 0, d);
    const t = engine.update({ ...p, heading: 0, speed: 30, timestamp: d }).triggered;
    if (t.length) heard.push(d);
  }
  assert.equal(heard.length, 1);
  assert.ok(heard[0] >= 2000 && heard[0] < 3000, `triggered at ${heard[0]} m, lake is abeam at 3000 m`);
});
