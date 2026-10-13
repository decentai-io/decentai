import { Injectable, TemplateRef, signal } from '@angular/core';
import { Subject } from 'rxjs';

/**
 * What a page puts in the app's top bar (website/main).
 *
 * The bar is the one row of chrome above every page. Most pages leave
 * it as it is: the brand, Stop everything, the person. A chat fills it
 * instead with its way back, its title and its own actions, so the two
 * are one row and not two. A page that fills the bar carries Stop
 * everything in a menu of its own and asks for it here.
 */
@Injectable({ providedIn: 'root' })
export class TopBarService {
  /** The open page's row, or null for the bar as it is. */
  readonly content = signal<TemplateRef<unknown> | null>(null);

  /** Stop everything of this person's, asked from a page's own menu:
   *  the bar asks before it stops, as its own button does. */
  readonly stopAsked = new Subject<void>();
}
