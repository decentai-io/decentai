import { Injectable } from '@angular/core';
import { MatDialog } from '@angular/material/dialog';
import { firstValueFrom } from 'rxjs';
import {
  Confirmation,
  ConfirmDialogComponent,
} from '../components/ui/confirm-dialog.component';

/**
 * Asks before something is lost, on the one confirm dialog.
 *
 *   if (await this.confirm.ask({ title: 'Forget this memory?', confirm: 'Forget', danger: true })) …
 */
@Injectable({ providedIn: 'root' })
export class ConfirmService {
  constructor(private dialog: MatDialog) {}

  /** True when the person went ahead; false for Cancel, Escape and a
   *  click outside. */
  async ask(asked: Confirmation): Promise<boolean> {
    const dialog = this.dialog.open<ConfirmDialogComponent, Confirmation, boolean>(
      ConfirmDialogComponent,
      { data: asked },
    );
    return (await firstValueFrom(dialog.afterClosed())) === true;
  }
}
