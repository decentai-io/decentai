import {
  Component, EventEmitter, Input, OnChanges, OnDestroy, OnInit, Output, TemplateRef, ViewChild,
} from '@angular/core';
import { MatDialog, MatDialogRef } from '@angular/material/dialog';

import { ShareEditor } from 'src/app/pages/app/share-editor';

/**
 * Who can see one thing, decided in a dialog: the same four choices
 * wherever a thing can be shared (only me, my groups, specific people,
 * everyone), then Save.
 *
 * The page holds the choice as a ShareEditor, opens it on the thing,
 * and puts <app-share-dialog> in its template while it is open; the
 * component opens the dialog shell as it appears and says `closed`
 * when the dialog goes. Saving is the page's: it knows which door the
 * owner map goes through.
 */
@Component({
  selector: 'app-share-dialog',
  standalone: false,
  templateUrl: './share-dialog.component.html',
})
export class ShareDialogComponent implements OnInit, OnChanges, OnDestroy {
  @Input({ required: true }) share!: ShareEditor;
  /** The question, as the dialog's title: "Who can see this file". */
  @Input() heading = 'Who can see this';
  /** What sharing it means, in a sentence. */
  @Input() note = '';
  /** Whether the person may share with the whole organization. */
  @Input() allowOrganization = false;
  /** Why the last save was refused. */
  @Input() problem = '';

  @Output() save = new EventEmitter<void>();
  @Output() closed = new EventEmitter<void>();

  @ViewChild('sheet', { static: true })
  private sheet!: TemplateRef<unknown>;
  private dialogRef?: MatDialogRef<unknown>;

  /** Save was pressed: what is missing is said. */
  tried = false;

  constructor(private dialog: MatDialog) {}

  ngOnInit(): void {
    this.dialogRef = this.dialog.open(this.sheet, { width: '480px', maxWidth: '100vw' });
    this.dialogRef.afterClosed().subscribe(() => {
      this.dialogRef = undefined;
      this.closed.emit();
    });
  }

  ngOnChanges(): void {
    // While the save is on its way the dialog stays.
    if (this.dialogRef) this.dialogRef.disableClose = this.share.saving;
  }

  ngOnDestroy(): void {
    this.dialogRef?.close();
  }

  /** Choosing groups or people with none ticked would save as private,
   *  which is not what was said. */
  get missing(): string {
    if (this.share.ready) return '';
    return this.share.mode === 'groups' ? 'Tick at least one group.' : 'Tick at least one person.';
  }

  submit(): void {
    this.tried = true;
    if (!this.missing && !this.share.saving) this.save.emit();
  }

  close(): void {
    if (!this.share.saving) this.dialogRef?.close();
  }
}
