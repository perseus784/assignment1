import { test } from 'node:test';
import assert from 'node:assert/strict';
import { distance, bearing, angleDiff, destination, pointAlongRoute, routeLength, inBounds } from '../js/geo.js';

test('distance matches a known value (Old Faithful to Grand Prismatic ≈ 7.3 km)', () => {
  const d = distance({ lat: 44.4605, lon: -110.8281 }, { lat: 44.5251, lon: -110.8382 });
  assert.ok(Math.abs(d - 7230) < 100, `got ${d}`);
});

test('bearing points the right way', () => {
  const o = { lat: 44, lon: -110 };
  assert.ok(Math.abs(bearing(o, { lat: 45, lon: -110 }) - 0) < 0.01);
  assert.ok(Math.abs(bearing(o, { lat: 44, lon: -109 }) - 90) < 1);
  assert.ok(Math.abs(bearing(o, { lat: 43, lon: -110 }) - 180) < 0.01);
});

test('angleDiff wraps around north', () => {
  assert.equal(angleDiff(350, 10), 20);
  assert.equal(angleDiff(10, 350), 20);
  assert.equal(angleDiff(90, 270), 180);
});

test('destination round-trips with distance and bearing', () => {
  const start = { lat: 44.5, lon: -110.8 };
  const end = destination(start, 45, 1000);
  assert.ok(Math.abs(distance(start, end) - 1000) < 0.5);
  assert.ok(Math.abs(bearing(start, end) - 45) < 0.1);
});

test('pointAlongRoute interpolates and clamps', () => {
  const a = { lat: 44.5, lon: -110.8 };
  const b = destination(a, 0, 1000);
  const c = destination(b, 90, 1000);
  const route = [a, b, c];
  assert.ok(Math.abs(routeLength(route) - 2000) < 1);
  const mid = pointAlongRoute(route, 500);
  assert.ok(Math.abs(distance(a, mid) - 500) < 1);
  assert.ok(angleDiff(mid.heading, 0) < 0.5);
  const second = pointAlongRoute(route, 1500);
  assert.ok(Math.abs(second.heading - 90) < 1);
  const end = pointAlongRoute(route, 99999);
  assert.ok(distance(end, c) < 0.5);
});

test('inBounds', () => {
  const b = { north: 45, south: 44, east: -110, west: -111 };
  assert.ok(inBounds({ lat: 44.5, lon: -110.5 }, b));
  assert.ok(!inBounds({ lat: 46, lon: -110.5 }, b));
});
