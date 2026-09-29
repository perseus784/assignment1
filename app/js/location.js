// Position sources: the real GPS, or a simulated drive along a pack's demo route.

import { enrichFix } from './engine.js';
import { pointAlongRoute, routeLength } from './geo.js';

export class GpsSource {
  constructor(onFix, onError) {
    this.onFix = onFix;
    this.onError = onError;
    this.watchId = null;
    this.prev = null;
  }

  start() {
    if (!('geolocation' in navigator)) {
      this.onError(new Error('This device has no GPS / location support.'));
      return;
    }
    this.watchId = navigator.geolocation.watchPosition(
      (pos) => {
        const { latitude, longitude, speed, heading, accuracy } = pos.coords;
        const fix = enrichFix(
          { lat: latitude, lon: longitude, speed, heading, accuracy, timestamp: pos.timestamp },
          this.prev,
        );
        // Ignore wildly inaccurate fixes (e.g. cell-tower only) – they cause false triggers.
        if (accuracy != null && accuracy > 250) return;
        this.prev = fix;
        this.onFix(fix);
      },
      (err) => this.onError(err),
      { enableHighAccuracy: true, maximumAge: 2000, timeout: 30000 },
    );
  }

  stop() {
    if (this.watchId != null) navigator.geolocation.clearWatch(this.watchId);
    this.watchId = null;
    this.prev = null;
  }
}

export class SimulatedSource {
  /**
   * @param {{lat:number, lon:number}[]} route
   * @param {number} speed  simulated vehicle speed in m/s
   * @param {number} timeScale  how many times faster than real time to drive
   */
  constructor(route, onFix, { speed = 15, timeScale = 3, onEnd } = {}) {
    this.route = route;
    this.onFix = onFix;
    this.speed = speed;
    this.timeScale = timeScale;
    this.onEnd = onEnd;
    this.length = routeLength(route);
    this.travelled = 0;
    this.timer = null;
    this.clock = Date.now();
  }

  get progress() {
    return this.length ? this.travelled / this.length : 1;
  }

  start() {
    const tickMs = 1000;
    const tick = () => {
      const p = pointAlongRoute(this.route, this.travelled);
      this.onFix({ lat: p.lat, lon: p.lon, heading: p.heading, speed: this.speed, accuracy: 5, timestamp: this.clock });
      if (this.travelled >= this.length) {
        this.stop();
        this.onEnd?.();
        return;
      }
      // Simulated time advances faster than wall time so demos don't take an hour.
      this.clock += tickMs * this.timeScale;
      this.travelled = Math.min(this.length, this.travelled + this.speed * (tickMs / 1000) * this.timeScale);
    };
    tick();
    this.timer = setInterval(tick, tickMs);
  }

  stop() {
    clearInterval(this.timer);
    this.timer = null;
  }
}
