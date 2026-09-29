// A heading-up "radar" of nearby places. Works fully offline (no map tiles needed).

import { toRad } from './geo.js';

export function drawRadar(canvas, { fix, places, rangeMeters = 3000 }) {
  const dpr = window.devicePixelRatio || 1;
  const size = canvas.clientWidth;
  if (canvas.width !== size * dpr) {
    canvas.width = size * dpr;
    canvas.height = size * dpr;
  }
  const ctx = canvas.getContext('2d');
  const styles = getComputedStyle(canvas);
  const color = (name) => styles.getPropertyValue(name).trim();
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, size, size);

  const c = size / 2;
  const r = c - 8;

  // Range rings
  ctx.strokeStyle = color('--radar-ring');
  ctx.lineWidth = 1;
  for (const f of [1, 2 / 3, 1 / 3]) {
    ctx.beginPath();
    ctx.arc(c, c, r * f, 0, Math.PI * 2);
    ctx.stroke();
  }

  if (!fix) return;
  const heading = fix.heading ?? 0;

  // North marker so the driver can orient themselves
  const nAngle = toRad(-heading) - Math.PI / 2;
  ctx.fillStyle = color('--muted');
  ctx.font = '600 12px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('N', c + Math.cos(nAngle) * (r - 10), c + Math.sin(nAngle) * (r - 10));

  for (const { poi, distance, bearing, played } of places) {
    if (distance > rangeMeters) continue;
    const a = toRad(bearing - heading) - Math.PI / 2;
    const d = (distance / rangeMeters) * r;
    const x = c + Math.cos(a) * d;
    const y = c + Math.sin(a) * d;
    ctx.fillStyle = played ? color('--muted') : color('--accent');
    ctx.globalAlpha = played ? 0.5 : 1;
    ctx.beginPath();
    ctx.arc(x, y, played ? 4 : 6, 0, Math.PI * 2);
    ctx.fill();
    if (!played) {
      ctx.fillStyle = color('--text');
      ctx.font = '500 11px system-ui, sans-serif';
      ctx.textAlign = x > c ? 'right' : 'left';
      ctx.fillText(poi.name, x + (x > c ? -9 : 9), y);
    }
    ctx.globalAlpha = 1;
  }

  // You (always pointing up)
  ctx.fillStyle = color('--you');
  ctx.beginPath();
  ctx.moveTo(c, c - 11);
  ctx.lineTo(c + 8, c + 9);
  ctx.lineTo(c, c + 4);
  ctx.lineTo(c - 8, c + 9);
  ctx.closePath();
  ctx.fill();
}
