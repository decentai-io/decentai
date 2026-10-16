import { Injectable, NgZone } from '@angular/core';

/** Who does a piece of speech in this browser: the computer itself, or
 *  the platform's speech (Settings:Speech). */
export type SpeechBy = 'device' | 'server';

/** How the browser's own recognizer hears: on this computer, through
 *  its maker's online service, or not at all. */
export type Hearing = 'local' | 'online' | 'none';

interface Chosen { dictation?: SpeechBy; reading?: SpeechBy }

/**
 * The computer's own speech, as the browser offers it: its voices for
 * reading a reply aloud, and its recognizer for writing down what is
 * said. A voice is used only when it is the computer's own
 * (`localService`), so reading never leaves it. The recognizer is used
 * on the computer where the browser can; where it can only send the
 * voice to its maker's service (Google for Chrome, Microsoft for Edge,
 * Apple for Safari), it is used only when the person chose that,
 * knowing it.
 *
 * Which is used is this browser's choice, kept here: a desk and a
 * phone want different answers.
 */
@Injectable({ providedIn: 'root' })
export class DeviceSpeechService {
  private static readonly KEY = 'decentai.speech_here';

  /** The language the browser is set to. */
  readonly language = (typeof navigator !== 'undefined' && navigator.language) || 'en-US';

  constructor(private zone: NgZone) {}

  // ── The choice ─────────────────────────────────────────────────────

  private get chosen(): Chosen {
    try {
      return JSON.parse(localStorage.getItem(DeviceSpeechService.KEY) || '{}') || {};
    } catch {
      return {};
    }
  }

  choose(change: Chosen): void {
    try {
      localStorage.setItem(DeviceSpeechService.KEY, JSON.stringify({ ...this.chosen, ...change }));
    } catch {
      // A browser that keeps nothing: the choice lasts as long as the page.
    }
  }

  /** Who reads replies aloud here. The computer's own voices unless
   *  the person chose the server or the computer has none. */
  async reading(serverCan: boolean): Promise<SpeechBy | null> {
    const own = !!(await this.voice());
    const wanted = this.chosen.reading;
    if (wanted === 'server' && serverCan) return 'server';
    if (own) return 'device';
    return serverCan ? 'server' : null;
  }

  /** Who writes down what is said here. The computer when it hears on
   *  its own; its maker's online service only when the person chose
   *  it; otherwise the server, where it can. */
  async dictation(serverCan: boolean): Promise<SpeechBy | null> {
    const hearing = await this.hearing();
    const wanted = this.chosen.dictation;
    if (wanted === 'server' && serverCan) return 'server';
    if (hearing === 'local') return 'device';
    if (hearing === 'online' && wanted === 'device') return 'device';
    return serverCan ? 'server' : null;
  }

  // ── Reading aloud ──────────────────────────────────────────────────

  /** One of the computer's own voices for the browser's language, or
   *  null. The list may arrive a moment after the page. */
  async voice(): Promise<SpeechSynthesisVoice | null> {
    if (typeof speechSynthesis === 'undefined') return null;
    let voices = speechSynthesis.getVoices();
    if (!voices.length) {
      voices = await new Promise<SpeechSynthesisVoice[]>((resolve) => {
        const done = () => resolve(speechSynthesis.getVoices());
        speechSynthesis.addEventListener('voiceschanged', done, { once: true });
        setTimeout(done, 1000);
      });
    }
    const own = voices.filter((voice) => voice.localService);
    const base = this.language.split('-')[0].toLowerCase();
    return own.find((voice) => voice.lang.toLowerCase() === this.language.toLowerCase())
      ?? own.find((voice) => voice.lang.toLowerCase().startsWith(base))
      ?? null;
  }

  /** One piece said by the computer, to its end. False when it could not. */
  async say(text: string): Promise<boolean> {
    const voice = await this.voice();
    if (!voice) return false;
    return new Promise<boolean>((resolve) => {
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.voice = voice;
      utterance.lang = voice.lang;
      utterance.onend = () => this.zone.run(() => resolve(true));
      // A cancel (the person pressed stop) ends it as an error too.
      utterance.onerror = () => this.zone.run(() => resolve(false));
      speechSynthesis.speak(utterance);
    });
  }

  silence(): void {
    if (typeof speechSynthesis !== 'undefined') speechSynthesis.cancel();
  }

  // ── Writing down what is said ──────────────────────────────────────

  private get Recognition(): any {
    const scope = window as any;
    return scope.SpeechRecognition || scope.webkitSpeechRecognition || null;
  }

  /** Whether the browser hears on this computer, only through its
   *  maker's service, or not at all. A browser that says nothing of
   *  where it hears is taken to send the voice away. */
  async hearing(): Promise<Hearing> {
    const Recognition = this.Recognition;
    if (!Recognition) return 'none';
    if (typeof Recognition.available !== 'function') return 'online';
    try {
      const status = await Recognition.available({ langs: [this.language], processLocally: true });
      return status === 'available' ? 'local' : 'online';
    } catch {
      return 'online';
    }
  }

  /** Listening until `stop` is called; what was heard, whole — or
   *  nothing — comes to `done`. */
  async listen(done: (text: string) => void, failed: (why: string) => void): Promise<{ stop: () => void }> {
    const Recognition = this.Recognition;
    if (!Recognition) throw new Error('This browser cannot write down speech.');
    const local = (await this.hearing()) === 'local';
    const recognition = new Recognition();
    recognition.lang = this.language;
    recognition.continuous = true;
    recognition.interimResults = false;
    if (local) recognition.processLocally = true;
    const heard: string[] = [];
    recognition.onresult = (event: any) => {
      for (let index = event.resultIndex; index < event.results.length; index++) {
        if (event.results[index].isFinal) heard.push(event.results[index][0].transcript.trim());
      }
    };
    recognition.onerror = (event: any) => this.zone.run(() => {
      if (event.error === 'aborted' || event.error === 'no-speech') return;
      failed(event.error === 'not-allowed'
        ? 'The microphone is not available — allow it in the browser and try again.'
        : 'This browser could not write that down.');
    });
    recognition.onend = () => this.zone.run(() => done(heard.join(' ').trim()));
    recognition.start();
    return { stop: () => recognition.stop() };
  }
}
