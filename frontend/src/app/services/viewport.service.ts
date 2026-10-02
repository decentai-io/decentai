import { Injectable, NgZone } from '@angular/core';
import { BehaviorSubject } from 'rxjs';

/** The window's width class, watched once for every page that lays
 *  itself out differently on a phone or beside a live view: narrow is
 *  a phone, wide is room for two columns. */
@Injectable({ providedIn: 'root' })
export class ViewportService {
  static readonly NARROW = '(max-width: 700px)';
  static readonly WIDE = '(min-width: 1200px)';

  readonly narrow$: BehaviorSubject<boolean>;
  readonly wide$: BehaviorSubject<boolean>;

  constructor(zone: NgZone) {
    this.narrow$ = this.watch(ViewportService.NARROW, zone);
    this.wide$ = this.watch(ViewportService.WIDE, zone);
  }

  get narrow(): boolean { return this.narrow$.value; }
  get wide(): boolean { return this.wide$.value; }

  private watch(query: string, zone: NgZone): BehaviorSubject<boolean> {
    const media = window.matchMedia(query);
    const subject = new BehaviorSubject<boolean>(media.matches);
    const update = () => {
      if (media.matches !== subject.value) zone.run(() => subject.next(media.matches));
    };
    media.addEventListener('change', update);
    // Some embedded and emulated windows resize without a media change
    // event; the resize event says the same thing a beat later.
    window.addEventListener('resize', update, { passive: true });
    return subject;
  }
}
