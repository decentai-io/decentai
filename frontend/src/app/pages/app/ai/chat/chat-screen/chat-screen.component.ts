import {
  Component, ElementRef, EventEmitter, Input, OnChanges, OnDestroy, Output,
  SimpleChanges, ViewChild,
} from '@angular/core';

/** One of the tabs open behind the picture, as a frame tells of it. */
export interface ScreenTab {
  /** Its place among them, from 1: what the person's hand names. */
  index: number; title: string; address: string; active: boolean;
}

/** One frame of a screen an agent shows, as the page keeps it. */
export interface ScreenView {
  call_id: string; src: string; width: number; height: number; frame: number;
  agent_name?: string;
  /** What is open behind the picture; none where the agent tells of none. */
  tabs?: ScreenTab[];
  /** The agent's latest step, in its own words, for the caption. */
  caption?: string;
  /** When this frame arrived, so a picture that is not moving says so. */
  at: number;
  /** The call that showed this screen has ended; the browser behind it
   *  may still be open, and the next run continues there. */
  idle?: boolean;
}

/** One thing the person did on the screen, as the runtime takes it. */
export type ScreenInputEvent =
  | { type: 'mouse'; action: 'down' | 'up' | 'move' | 'wheel'; x: number; y: number; button?: string; deltaX?: number; deltaY?: number }
  | { type: 'key'; action: 'down' | 'up'; key: string; code?: string; text?: string; modifiers?: string[] }
  | { type: 'control'; action: 'take' | 'release' | 'close' }
  | { type: 'navigate'; url: string }
  | { type: 'tab'; action: 'switch' | 'close' | 'new'; index?: number };

/**
 * The live view: what an agent is driving, as pictures, and the
 * person's hand on it when they take over.
 *
 * Frames arrive as socket-only events and are drawn as they come;
 * nothing is kept. While the person holds control, their pointer,
 * keys and wheel on the picture are scaled to the frame's own size and
 * sent in small batches — the road back is the chat socket, and the
 * agent feeds them to whatever it drives. Hand back returns control.
 */
@Component({
  selector: 'app-chat-screen',
  standalone: false,
  templateUrl: './chat-screen.component.html',
  styleUrls: ['./chat-screen.component.css'],
})
export class ChatScreenComponent implements OnChanges, OnDestroy {
  @Input() frame: ScreenView | null = null;
  /** Standing beside the thread rather than inside it: the view takes
   *  the column's height, and Larger has nothing to add. */
  @Input() side = false;
  /** What the person did on the screen, in small batches. Not named
   *  `input`: the address box's own input events bubble to this
   *  element, and a listener on that name would hear them too. */
  @Output() screenInput = new EventEmitter<ScreenInputEvent[]>();
  /** The person closed the panel: hidden by the page, and the call
   *  that only shows is told to end. The browser stays. */
  @Output() closed = new EventEmitter<void>();
  /** The person closed the browser itself: it goes, and where it was
   *  is remembered for the next open. */
  @Output() quit = new EventEmitter<void>();
  /** Folded to its head: still here, out of the way. The page keeps
   *  it, so the column beside the thread gives the room back and the
   *  view stays folded when the window changes shape. */
  @Input() minimized = false;
  @Output() minimizedChange = new EventEmitter<boolean>();

  /** The picture is drawn on a canvas, not shown by an image: an
   *  image swapping its source goes blank while the next frame
   *  decodes, and at ten frames a second that reads as blinking. The
   *  canvas keeps the last frame until the next one is ready. */
  @ViewChild('picture') set pictureRef(ref: ElementRef<HTMLCanvasElement> | undefined) {
    // A canvas that has just appeared — the first frame, or the view
    // opened again after it was folded — is empty until it is painted.
    this.picture = ref;
    if (ref && this.frame) this.draw(this.frame.src);
  }
  private picture?: ElementRef<HTMLCanvasElement>;
  private drawn = 0;

  /** Whether the person holds control. The page keeps it: the view is
   *  built again when it moves between the thread and its side — a
   *  window resized, a phone turned — and the hand on the browser must
   *  not be lost, or silently kept, by that. */
  @Input() taken = false;
  @Output() takenChange = new EventEmitter<boolean>();
  /** A larger view, when the picture deserves the room. */
  expanded = false;
  /** Where the person wants the browser to go — sent as an input the
   *  agent takes to the page through its own address policy, so a
   *  blank browser is not a dead end. */
  address = '';
  /** How old the frame on screen is, in whole seconds, ticked once a
   *  second: a picture that has not moved for a while says so, since a
   *  still frame and a stuck one look the same. */
  age = 0;
  private ticker: ReturnType<typeof setInterval> | null = null;

  /** How often batched events go out while the person acts: short,
   *  since every batch is one round trip between their hand and the
   *  page; a move still replaces the move before it. */
  private static readonly BATCH_MS = 20;
  private pending: ScreenInputEvent[] = [];
  private timer: ReturnType<typeof setTimeout> | null = null;

  constructor() {
    this.ticker = setInterval(() => {
      this.age = this.frame ? Math.max(0, Math.round((Date.now() - this.frame.at) / 1000)) : 0;
    }, 1000);
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['frame'] && this.frame) this.draw(this.frame.src);
  }

  /** Decode off screen, then paint in one go; a frame that arrives
   *  after a newer one was painted is dropped. */
  private draw(src: string): void {
    const ticket = ++this.drawn;
    const image = new Image();
    image.onload = () => {
      if (ticket !== this.drawn) return;
      const canvas = this.picture?.nativeElement;
      if (!canvas) return;
      if (canvas.width !== image.naturalWidth || canvas.height !== image.naturalHeight) {
        canvas.width = image.naturalWidth;
        canvas.height = image.naturalHeight;
      }
      canvas.getContext('2d')?.drawImage(image, 0, 0);
    };
    image.src = src;
  }

  /** What is still waiting goes out now: a hand-back queued as the
   *  panel closed is the last thing this view says, and dropping it
   *  would leave the agent waiting on a person who has gone. */
  ngOnDestroy(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    while (this.pending.length) this.screenInput.emit(this.pending.splice(0, 64));
    if (this.ticker) clearInterval(this.ticker);
  }

  private hold(taken: boolean): void {
    this.taken = taken;
    this.takenChange.emit(taken);
  }

  /** What the head says about the picture: whose hand, and how old a
   *  picture that stopped moving is. The first seconds are silent —
   *  a model thinking between steps is not news. */
  get status(): string {
    if (this.taken) return 'You are in control — Esc to hand back';
    if (this.frame?.idle) return 'Idle — the browser stays open for the next run';
    const stale = this.age >= 4 ? ` · ${this.age}s ago` : '';
    return `Watching — Enter to take over${stale}`;
  }

  get title(): string {
    return `Live view${this.frame?.agent_name ? ' — ' + this.frame.agent_name : ''}`;
  }

  takeOver(): void {
    if (this.taken || this.frame?.idle) return;
    this.hold(true);
    this.queue({ type: 'control', action: 'take' });
    queueMicrotask(() => this.picture?.nativeElement?.focus());
  }

  handBack(): void {
    if (!this.taken) return;
    this.hold(false);
    this.queue({ type: 'control', action: 'release' });
  }

  toggleExpanded(): void {
    this.expanded = !this.expanded;
    if (this.expanded && this.minimized) {
      this.minimized = false;
      this.minimizedChange.emit(false);
    }
  }

  toggleMinimized(): void {
    this.minimized = !this.minimized;
    if (this.minimized && this.taken) this.handBack();
    this.minimizedChange.emit(this.minimized);
  }

  close(): void {
    if (this.taken) this.handBack();
    this.closed.emit();
  }

  quitBrowser(): void {
    if (this.taken) this.handBack();
    this.quit.emit();
  }

  /** The person's hand on the tabs: to one, away with one, a new one.
   *  A hand on a tab is a hand on the browser, so it takes control
   *  first — a run gives way — and the tab follows in the same batch. */
  onTab(action: 'switch' | 'close' | 'new', tab?: ScreenTab): void {
    if (!this.frame || this.frame.idle) return;
    // The tab in front is where the person already is.
    if (action === 'switch' && tab?.active) return;
    if (!this.taken) {
      this.hold(true);
      this.queue({ type: 'control', action: 'take' });
    }
    this.queue(tab ? { type: 'tab', action, index: tab.index } : { type: 'tab', action });
    queueMicrotask(() => this.picture?.nativeElement?.focus());
  }

  go(): void {
    const url = this.address.trim();
    if (!url) return;
    this.screenInput.emit([{ type: 'navigate', url: /^[a-z]+:\/\//i.test(url) ? url : `https://${url}` }]);
    this.address = '';
  }

  // -- the person's hand -----------------------------------------------------
  onPointer(event: PointerEvent, action: 'down' | 'up' | 'move'): void {
    if (!this.taken) return;
    event.preventDefault();
    const at = this.scaled(event);
    if (!at) return;
    const button = event.button === 2 ? 'right' : event.button === 1 ? 'middle' : 'left';
    this.queue({ type: 'mouse', action, ...at, button }, action === 'move');
  }

  onWheel(event: WheelEvent): void {
    if (!this.taken) return;
    event.preventDefault();
    const at = this.scaled(event);
    if (!at) return;
    this.queue({ type: 'mouse', action: 'wheel', ...at, deltaX: Math.round(event.deltaX), deltaY: Math.round(event.deltaY) });
  }

  onKey(event: KeyboardEvent, action: 'down' | 'up'): void {
    if (!this.taken) {
      // Watching: Enter on the picture takes it over; nothing else is sent.
      if (event.key === 'Enter' && action === 'down') {
        event.preventDefault();
        this.takeOver();
      }
      return;
    }
    // Escape hands control back; everything else is the page's.
    if (event.key === 'Escape' && action === 'down') {
      this.handBack();
      return;
    }
    event.preventDefault();
    const modifiers = [
      event.ctrlKey ? 'Control' : '', event.shiftKey ? 'Shift' : '',
      event.altKey ? 'Alt' : '', event.metaKey ? 'Meta' : '',
    ].filter(Boolean);
    this.queue({
      type: 'key', action, key: event.key, code: event.code,
      text: action === 'down' && event.key.length === 1 && !event.ctrlKey && !event.metaKey ? event.key : '',
      modifiers,
    });
  }

  onContextMenu(event: MouseEvent): void {
    if (this.taken) event.preventDefault();
  }

  /** Page pixels on the picture → the frame's own pixels. */
  private scaled(event: MouseEvent): { x: number; y: number } | null {
    const image = this.picture?.nativeElement;
    if (!image || !this.frame) return null;
    const box = image.getBoundingClientRect();
    if (!box.width || !box.height) return null;
    const x = Math.round(((event.clientX - box.left) / box.width) * this.frame.width);
    const y = Math.round(((event.clientY - box.top) / box.height) * this.frame.height);
    return { x: Math.max(0, Math.min(this.frame.width, x)), y: Math.max(0, Math.min(this.frame.height, y)) };
  }

  /** Batched every few tens of milliseconds; a move replaces the
   *  previous move still waiting, so a fast hand is not a flood. */
  private queue(event: ScreenInputEvent, coalesce = false): void {
    if (coalesce) {
      const last = this.pending[this.pending.length - 1];
      if (last && last.type === 'mouse' && last.action === 'move') this.pending.pop();
    }
    this.pending.push(event);
    if (!this.timer) {
      this.timer = setTimeout(() => this.flush(), ChatScreenComponent.BATCH_MS);
    }
  }

  private flush(): void {
    this.timer = null;
    if (!this.pending.length) return;
    const batch = this.pending.splice(0, 64);
    this.screenInput.emit(batch);
    if (this.pending.length) this.timer = setTimeout(() => this.flush(), ChatScreenComponent.BATCH_MS);
  }
}
