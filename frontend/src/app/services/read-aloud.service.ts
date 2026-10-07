import { Injectable, NgZone } from '@angular/core';

import { SettingsSpeechService } from './settings-speech.service';

/** A reply said aloud: one at a time, in pieces, so the first sentence
 *  is heard while the rest is still being made.
 *
 *  The organization decides whether replies can be said at all
 *  (Settings:Speech). Whether they are said without being asked is the
 *  person's, and this device's: a phone in a meeting and a desk at home
 *  want different answers, so the choice is kept in this browser. */
@Injectable({ providedIn: 'root' })
export class ReadAloudService {
  /** About a sentence or two a piece: short enough that the first is
   *  ready quickly, long enough that a voice does not stop mid-thought. */
  static readonly PIECE = 320;
  /** The most of one reply that is said. A reply longer than this is a
   *  document, and is read. */
  static readonly MOST = 6000;
  private static readonly KEY = 'decentai.read_aloud';

  /** Whether this organization says replies at all. */
  available = false;
  /** The reply being said, by its key; '' for none. */
  speaking = '';
  /** True from the request until the first piece is heard. */
  preparing = false;
  /** Why the last one could not be said, and which reply that was;
   *  cleared by the next. */
  problem = '';
  problemAt = '';

  private audio: HTMLAudioElement | null = null;
  private address = '';
  /** Each request to speak has a number; a newer one, or a stop,
   *  makes an older one's pieces unwanted wherever they are. */
  private turn = 0;
  private heard = new Set<string>();

  constructor(private speech: SettingsSpeechService, private zone: NgZone) {}

  /** Asked as a chat opens: whether to show the control at all. */
  async refresh(): Promise<boolean> {
    this.available = (await this.speech.offered()).speech.configured;
    return this.available;
  }

  /** Replies said as they arrive, on this device. */
  get automatic(): boolean {
    try {
      return localStorage.getItem(ReadAloudService.KEY) === '1';
    } catch {
      return false;
    }
  }

  set automatic(on: boolean) {
    try {
      if (on) localStorage.setItem(ReadAloudService.KEY, '1');
      else localStorage.removeItem(ReadAloudService.KEY);
    } catch {
      // A browser that keeps nothing: the choice lasts as long as the page.
    }
    if (!on) this.stop();
  }

  /** The control on a reply: say it, or stop saying it. */
  toggle(key: string, text: string): void {
    if (this.speaking === key) this.stop();
    else void this.say(key, text);
  }

  /** A reply that has just arrived: said if the person asked for that,
   *  and once only — a connection that comes back replays what was
   *  already said. */
  arrived(key: string, text: string, createdAt?: string): void {
    if (!this.available || !this.automatic || !key || this.heard.has(key)) return;
    this.heard.add(key);
    const made = createdAt ? new Date(createdAt).getTime() : Date.now();
    if (!isNaN(made) && Date.now() - made > 60000) return;
    void this.say(key, text);
  }

  stop(): void {
    this.turn++;
    this.speaking = '';
    this.preparing = false;
    this.silence();
  }

  private silence(): void {
    if (this.audio) {
      this.audio.onended = null;
      this.audio.onerror = null;
      this.audio.pause();
      this.audio = null;
    }
    if (this.address) URL.revokeObjectURL(this.address);
    this.address = '';
  }

  private async say(key: string, text: string): Promise<void> {
    const pieces = ReadAloudService.pieces(text);
    this.stop();
    if (!pieces.length) return;
    const turn = this.turn;
    this.speaking = key;
    this.preparing = true;
    this.problem = '';
    this.problemAt = '';
    this.heard.add(key);

    // The next piece is asked for while this one plays, never more
    // than one ahead: a reply stopped early has cost one piece.
    let next = this.speech.speak(pieces[0]);
    for (let index = 0; index < pieces.length; index++) {
      const made = await next;
      if (turn !== this.turn) return;
      if (!made.audio) {
        this.problem = made.error || 'That could not be said.';
        this.problemAt = key;
        this.stop();
        return;
      }
      next = index + 1 < pieces.length
        ? this.speech.speak(pieces[index + 1])
        : Promise.resolve({});
      this.preparing = false;
      const played = await this.play(made.audio);
      if (turn !== this.turn) return;
      if (!played) {
        this.problem = 'This browser would not play it. Press the control on the reply to hear it.';
        this.problemAt = key;
        this.stop();
        return;
      }
    }
    if (turn === this.turn) this.stop();
  }

  /** One piece, to its end. False when the browser refused to play. */
  private play(blob: Blob): Promise<boolean> {
    this.silence();
    this.address = URL.createObjectURL(blob);
    const audio = new Audio(this.address);
    this.audio = audio;
    return new Promise<boolean>((resolve) => {
      // The element's events arrive outside Angular's zone; the page
      // would go on showing a reply as being said after it had ended.
      audio.onended = () => this.zone.run(() => resolve(true));
      audio.onerror = () => this.zone.run(() => resolve(false));
      audio.play().catch(() => this.zone.run(() => resolve(false)));
    });
  }

  /** A reply cut where a voice would pause: at a line's end, then at a
   *  sentence's, and gathered into pieces of about PIECE characters.
   *  Code is left out here as it is at the far end — nobody says it. */
  static pieces(text: string): string[] {
    const plain = String(text || '')
      .replace(/```[\s\S]*?(```|$)/g, ' ')
      .slice(0, ReadAloudService.MOST);
    const sentences: string[] = [];
    for (const line of plain.split(/\n+/)) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      // After a full stop, a question or an exclamation — Latin's or
      // Arabic's — and the space that follows it.
      sentences.push(...trimmed.split(/(?<=[.!?؟۔])\s+/).filter(Boolean));
    }
    const pieces: string[] = [];
    let piece = '';
    for (const sentence of sentences) {
      if (piece && piece.length + sentence.length + 1 > ReadAloudService.PIECE) {
        pieces.push(piece);
        piece = '';
      }
      piece = piece ? `${piece} ${sentence}` : sentence;
      // One sentence longer than a piece is cut at a space.
      while (piece.length > ReadAloudService.PIECE * 2) {
        const cut = piece.lastIndexOf(' ', ReadAloudService.PIECE * 2);
        const at = cut > ReadAloudService.PIECE ? cut : ReadAloudService.PIECE * 2;
        pieces.push(piece.slice(0, at).trim());
        piece = piece.slice(at).trim();
      }
    }
    if (piece) pieces.push(piece);
    // What is all marks and no letters says nothing.
    return pieces.filter((part) => /[\p{L}\p{N}]/u.test(part));
  }
}
