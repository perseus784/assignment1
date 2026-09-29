// On-device text-to-speech, used when a tour has no pre-rendered narration.
// Two roles (narrator, storyteller) get two different voices where the device has them.

const WORDS_PER_SECOND = 2.6;

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

export function estimateSeconds(text, rate = 1) {
  return text.split(/\s+/).length / (WORDS_PER_SECOND * rate);
}

/** Rank voices: on-device (works offline) and higher quality first. */
export function rankVoices(voices, lang = 'en') {
  const quality = (v) => (/(natural|neural|enhanced|premium|siri)/i.test(v.name) ? 2 : 0) + (v.localService ? 1 : 0);
  return voices
    .filter((v) => v.lang?.toLowerCase().startsWith(lang))
    .sort((a, b) => quality(b) - quality(a) || (b.lang === 'en-US') - (a.lang === 'en-US'));
}

export class Speech {
  constructor() {
    this.synth = typeof speechSynthesis !== 'undefined' ? speechSynthesis : null;
    this.voices = { narrator: null, storyteller: null };
    this.style = { narrator: { pitch: 0.92, rate: 0.97 }, storyteller: { pitch: 1.05, rate: 1.0 } };
    this.rate = 1;
    if (this.synth) {
      this.pick();
      this.synth.addEventListener?.('voiceschanged', () => this.pick());
    }
  }

  get supported() {
    return !!this.synth;
  }

  pick() {
    const ranked = rankVoices(this.synth.getVoices());
    this.voices.narrator = ranked[0] ?? null;
    // A different voice for the storyteller if we have one; otherwise pitch/rate carry the contrast.
    this.voices.storyteller = ranked.find((v) => v !== ranked[0] && v.lang === ranked[0]?.lang) ?? ranked[1] ?? ranked[0] ?? null;
  }

  setStyle(voices) {
    for (const role of ['narrator', 'storyteller']) {
      if (voices?.[role]?.device) this.style[role] = { ...this.style[role], ...voices[role].device };
    }
  }

  unlock() {
    if (!this.synth) return;
    const u = new SpeechSynthesisUtterance(' ');
    u.volume = 0;
    this.synth.speak(u);
  }

  cancel() {
    this.synth?.cancel();
  }

  /** Speak `text` in a role; resolves when done (or after a safety timeout). */
  speak(text, role = 'narrator', { signal } = {}) {
    return new Promise((resolve) => {
      const style = this.style[role] ?? this.style.narrator;
      const rate = (style.rate ?? 1) * this.rate;
      const limit = estimateSeconds(text, rate) * 1600 + 4000;
      let settled = false;
      const done = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        resolve();
      };
      const timer = setTimeout(done, this.synth ? limit : estimateSeconds(text, rate) * 1000);
      signal?.addEventListener('abort', () => { this.cancel(); done(); });
      if (!this.synth) return;
      // Long utterances get cut off on some engines, so speak sentence-sized chunks.
      const chunks = splitSentences(text);
      chunks.forEach((chunk, i) => {
        const u = new SpeechSynthesisUtterance(chunk);
        const voice = this.voices[role] ?? this.voices.narrator;
        if (voice) u.voice = voice;
        u.lang = voice?.lang ?? 'en-US';
        u.rate = rate;
        u.pitch = style.pitch ?? 1;
        if (i === chunks.length - 1) {
          u.onend = done;
          u.onerror = done;
        }
        this.synth.speak(u);
      });
    });
  }
}
