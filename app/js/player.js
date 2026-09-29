// Plays one episode: a script of ambience beds, sound effects, music stings and
// narration, mixed live with Web Audio. Beds duck under the voice like a produced podcast.

import { Speech, estimateSeconds } from './speech.js';

const DUCK_LEVEL = 0.32;
const MP3_EDGE = 0.06; // skip encoder padding at both ends so loops are seamless

const sleep = (ms, signal) =>
  new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => { clearTimeout(t); resolve(); });
  });

export class EpisodePlayer {
  /**
   * @param {object} opts
   * @param {(path: string) => string} opts.resolve  asset path -> URL
   * @param {(line: {text: string, voice: string}|null) => void} [opts.onLine]
   */
  constructor({ resolve, onLine } = {}) {
    this.resolve = resolve ?? ((p) => p);
    this.onLine = onLine ?? (() => {});
    this.speech = new Speech();
    this.ctx = null;
    this.buffers = new Map();
    this.muted = false;
    this.volume = { beds: 1, effects: 1 };
    this.controller = null;
  }

  get speechSupported() {
    return this.speech.supported;
  }

  /** Must run inside a user gesture (tap) before audio can play on mobile. */
  unlock() {
    if (!this.ctx) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new Ctx();
      this.master = this.ctx.createGain();
      this.master.connect(this.ctx.destination);
      this.bedBus = this.ctx.createGain();
      this.bedBus.connect(this.master);
      this.fxBus = this.ctx.createGain();
      this.fxBus.connect(this.master);
      this.voiceBus = this.ctx.createGain();
      this.voiceBus.connect(this.master);
    }
    this.ctx.resume();
    this.speech.unlock();
  }

  setMuted(muted) {
    this.muted = muted;
    if (this.master) this.master.gain.setTargetAtTime(muted ? 0 : 1, this.ctx.currentTime, 0.05);
    if (muted) this.speech.cancel();
  }

  load(path, base) {
    const url = base ? new URL(path, base).href : this.resolve(path);
    if (!this.buffers.has(url)) {
      const p = fetch(url)
        .then((r) => {
          if (!r.ok) throw new Error(`${r.status} ${url}`);
          return r.arrayBuffer();
        })
        .then((data) => this.ctx.decodeAudioData(data));
      p.catch(() => this.buffers.delete(url));
      this.buffers.set(url, p);
    }
    return this.buffers.get(url);
  }

  /** Warm the decode cache for an episode so playback starts instantly. */
  preload(episode, base) {
    if (!this.ctx) return;
    for (const seg of episode.segments) if (seg.audio) this.load(seg.audio, base).catch(() => {});
  }

  stop() {
    this.controller?.abort();
  }

  /**
   * Play an episode to the end (or until stopped).
   * @param {object} episode
   * @param {{base?: string}} [opts] URL of the manifest the episode's audio paths are relative to
   */
  async play(episode, { base } = {}) {
    this.stop();
    const controller = new AbortController();
    this.controller = controller;
    const { signal } = controller;
    const beds = [];
    const oneShots = [];
    const ctx = this.ctx;

    const startSource = (buffer, bus, gain, { loop = false, fadeIn = 0 } = {}) => {
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      const g = ctx.createGain();
      src.connect(g).connect(bus);
      const now = ctx.currentTime;
      if (fadeIn) {
        g.gain.setValueAtTime(0.0001, now);
        g.gain.exponentialRampToValueAtTime(Math.max(gain, 0.0002), now + fadeIn);
      } else {
        g.gain.value = gain;
      }
      if (loop) {
        src.loop = true;
        src.loopStart = MP3_EDGE;
        src.loopEnd = Math.max(MP3_EDGE * 2, buffer.duration - MP3_EDGE);
        src.start(now, MP3_EDGE);
      } else {
        src.start(now);
      }
      return { src, g };
    };

    const duck = (on) => {
      const t = ctx.currentTime;
      this.bedBus.gain.cancelScheduledValues(t);
      this.bedBus.gain.setTargetAtTime(on ? DUCK_LEVEL : 1, t, on ? 0.08 : 0.35);
    };

    try {
      for (const seg of episode.segments) {
        if (signal.aborted) break;
        if (seg.type === 'pause') {
          await sleep((seg.seconds ?? 0.6) * 1000, signal);
        } else if (seg.type === 'bed') {
          const buffer = await this.load(seg.audio, base).catch(() => null);
          if (!buffer || signal.aborted) continue;
          if (beds.length >= 2) fadeOut(ctx, beds.shift(), 1.5);
          beds.push(startSource(buffer, this.bedBus, (seg.gain ?? 0.5) * this.volume.beds, { loop: true, fadeIn: 1.5 }));
        } else if (seg.type === 'sfx' || seg.type === 'music') {
          const buffer = await this.load(seg.audio, base).catch(() => null);
          if (!buffer || signal.aborted) continue;
          const node = startSource(buffer, this.fxBus, (seg.gain ?? 0.7) * this.volume.effects);
          oneShots.push(node);
          if (seg.wait) await Promise.race([ended(node.src), sleep(buffer.duration * 1000 + 200, signal)]);
        } else if (seg.type === 'say') {
          this.onLine({ text: seg.text, voice: seg.voice });
          duck(true);
          if (seg.audio) {
            const buffer = await this.load(seg.audio, base).catch(() => null);
            if (buffer && !signal.aborted) {
              const node = startSource(buffer, this.voiceBus, 1);
              oneShots.push(node);
              await Promise.race([ended(node.src), sleep(buffer.duration * 1000 + 300, signal)]);
            }
          } else if (this.muted) {
            await sleep(estimateSeconds(seg.text) * 1000, signal); // keep the transcript moving
          } else {
            await this.speech.speak(seg.text, seg.voice, { signal });
          }
          duck(false);
        }
      }
    } finally {
      this.onLine(null);
      if (signal.aborted) {
        this.speech.cancel();
        oneShots.forEach((n) => fadeOut(ctx, n, 0.15));
        beds.forEach((n) => fadeOut(ctx, n, 0.4));
        duck(false);
      } else {
        beds.forEach((n) => fadeOut(ctx, n, 2.5));
        await sleep(600);
      }
      if (this.controller === controller) this.controller = null;
    }
    return !signal.aborted;
  }
}

function ended(src) {
  return new Promise((resolve) => { src.onended = resolve; });
}

function fadeOut(ctx, node, seconds) {
  const t = ctx.currentTime;
  try {
    node.g.gain.cancelScheduledValues(t);
    node.g.gain.setValueAtTime(node.g.gain.value || 0.0001, t);
    node.g.gain.exponentialRampToValueAtTime(0.0001, t + seconds);
    node.src.stop(t + seconds + 0.05);
  } catch { /* already stopped */ }
}
