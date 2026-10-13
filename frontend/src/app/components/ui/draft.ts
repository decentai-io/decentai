/**
 * What a form is editing, beside what is saved: the page binds its
 * controls to `value`, asks `dirty` whether the save bar shows, and
 * calls `take` with what the server answered or `discard` to go back.
 *
 *   readonly draft = new Draft<SpeechSettings>(DEFAULTS);
 *   … [(ngModel)]="draft.value.speech_voice" …
 *   <app-save-bar [dirty]="draft.dirty" (discard)="draft.discard()" …>
 */
export class Draft<T> {
  /** What the form shows and the person changes. */
  value: T;
  private saved: T;

  constructor(initial: T) {
    this.saved = structuredClone(initial);
    this.value = structuredClone(initial);
  }

  /** The form differs from what is saved. */
  get dirty(): boolean {
    return JSON.stringify(this.value) !== JSON.stringify(this.saved);
  }

  /** What is saved now: read from the server, or answered by a save. */
  take(saved: T): void {
    this.saved = structuredClone(saved);
    this.value = structuredClone(saved);
  }

  /** Back to what is saved. */
  discard(): void {
    this.value = structuredClone(this.saved);
  }
}
