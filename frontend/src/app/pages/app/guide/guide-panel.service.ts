import { Injectable } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';

import { GuidePanelComponent, GuidePanelData } from './guide-panel.component';

/**
 * Opens a chapter of the Guide beside the page that asked: a panel at
 * the right of a wide screen, and the sheet every dialog is on a
 * narrow one.
 */
@Injectable({ providedIn: 'root' })
export class GuidePanelService {
  constructor(private dialog: MatDialog) {}

  /** `guide` is a chapter, with a heading after a #: "chats#approvals". */
  open(guide: string): void {
    const [chapter, heading] = guide.split('#');
    const wide = window.matchMedia('(min-width: 701px)').matches;
    this.dialog.open<GuidePanelComponent, GuidePanelData>(GuidePanelComponent, {
      data: { chapter, heading },
      width: '440px',
      maxWidth: '100vw',
      autoFocus: 'dialog',
      // At the right edge, as tall as the window; a narrow screen
      // places every dialog as a sheet by itself.
      ...(wide ? { height: '100vh', position: { right: '0', top: '0' }, panelClass: 'side-sheet' } : {}),
    });
  }
}
