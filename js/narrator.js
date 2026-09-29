// Speaks stories one at a time using the device's built-in (offline) text-to-speech.

const WORDS_PER_SECOND = 2.6;

export class Narrator {
  constructor({ onChange } = {}) {
    this.queue = [];
    this.current = null;
    this.muted = false;
    this.rate = 1;
    this.onChange = onChange ?? (() => {});
    this.synth = typeof speechSynthesis !== 'undefined' ? speechSynthesis : null;
    this.voice = null;
    this.audioCtx = null;
    this._timer = null;
    this._token = 0;
    if (this.synth) {
      this.pickVoice();
      this.synth.addEventListener?.('voiceschanged', () => this.pickVoice());
    }
  }

  get supported() {
    return !!this.synth;
  }

  /** Prefer an on-device English voice so narration keeps working with no signal. */
  pickVoice() {
    const voices = this.synth.getVoices();
    const english = voices.filter((v) => v.lang?.toLowerCase().startsWith('en'));
    this.voice =
      english.find((v) => v.localService && /natural|enhanced|premium/i.test(v.name)) ??
      english.find((v) => v.localService) ??
      english[0] ??
      null;
  }

  /** Must be called from a user gesture (tap) so iOS/Chrome allow audio later on. */
  unlock() {
    try {
      this.audioCtx ??= new (window.AudioContext || window.webkitAudioContext)();
      this.audioCtx.resume();
    } catch { /* audio unavailable */ }
    if (this.synth) {
      const u = new SpeechSynthesisUtterance(' ');
      u.volume = 0;
      this.synth.speak(u);
    }
  }

  /** @param {{id:string, title:string, text:string, stillRelevant?:() => boolean}} item */
  enqueue(item) {
    if (this.current?.id === item.id || this.queue.some((q) => q.id === item.id)) return;
    this.queue.push(item);
    this.onChange();
    if (!this.current) this._next();
  }

  skip() {
    this._stopSpeech();
    this._next();
  }

  stop() {
    this.queue = [];
    this._stopSpeech();
    this.current = null;
    this.onChange();
  }

  replay(item) {
    this.queue.unshift(item);
    this.skip();
  }

  setMuted(muted) {
    this.muted = muted;
    if (muted) this._stopSpeech(); // keep the text on screen, just stop talking
    this.onChange();
  }

  _stopSpeech() {
    this._token++;
    clearTimeout(this._timer);
    this.synth?.cancel();
  }

  _next() {
    clearTimeout(this._timer);
    let item = this.queue.shift();
    // Drop stories for places you've already driven well past.
    while (item && item.stillRelevant && !item.stillRelevant()) item = this.queue.shift();
    this.current = item ?? null;
    this.onChange();
    if (!item) return;

    const token = ++this._token;
    const done = () => {
      if (token !== this._token) return;
      this._token++;
      setTimeout(() => this._next(), 1200); // short breather between stories
    };

    // Safety net: some engines never fire `onend` (or there is no TTS at all).
    const words = `${item.title} ${item.text}`.split(/\s+/).length;
    const estimateMs = (words / (WORDS_PER_SECOND * this.rate)) * 1000;
    this._timer = setTimeout(done, this.muted || !this.synth ? estimateMs + 2000 : estimateMs * 1.6 + 5000);

    if (this.muted || !this.synth) return;
    this._chime();
    // Long utterances get cut off on some platforms, so speak sentence-sized chunks.
    const chunks = splitSentences(`${item.title}. ${item.text}`);
    chunks.forEach((chunk, i) => {
      const u = new SpeechSynthesisUtterance(chunk);
      if (this.voice) u.voice = this.voice;
      u.lang = this.voice?.lang ?? 'en-US';
      u.rate = this.rate;
      if (i === chunks.length - 1) {
        u.onend = done;
        u.onerror = done;
      }
      setTimeout(() => token === this._token && this.synth.speak(u), i === 0 ? 450 : 0);
    });
  }

  _chime() {
    const ctx = this.audioCtx;
    if (!ctx) return;
    const now = ctx.currentTime;
    [660, 880].forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, now + i * 0.15);
      gain.gain.exponentialRampToValueAtTime(0.15, now + i * 0.15 + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + i * 0.15 + 0.3);
      osc.connect(gain).connect(ctx.destination);
      osc.start(now + i * 0.15);
      osc.stop(now + i * 0.15 + 0.32);
    });
  }
}

export function splitSentences(text, maxLen = 220) {
  const sentences = text.match(/[^.!?]+[.!?]+["')\]]*|[^.!?]+$/g) ?? [text];
  const chunks = [];
  let buf = '';
  for (const s of sentences.map((x) => x.trim()).filter(Boolean)) {
    if (buf && (buf + ' ' + s).length > maxLen) {
      chunks.push(buf);
      buf = s;
    } else {
      buf = buf ? `${buf} ${s}` : s;
    }
  }
  if (buf) chunks.push(buf);
  return chunks;
}
