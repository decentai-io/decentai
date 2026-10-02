import { Component } from '@angular/core';
import { MatDialogRef } from '@angular/material/dialog';
import { FileChoice } from 'src/app/models/chat-protocol';

/**
 * "Choose from your files" in the composer: the picker in a dialog,
 * the newest few leading, and the choice handed back to the composer
 * as attachments-to-be. Nothing is uploaded — these files are already
 * the platform's, and a message names them by reference.
 */
@Component({
  selector: 'app-chat-file-picker-dialog',
  standalone: false,
  templateUrl: './chat-file-picker-dialog.component.html',
  styleUrls: ['./chat-file-picker-dialog.component.css'],
})
export class ChatFilePickerDialogComponent {
  chosen: FileChoice[] = [];

  constructor(private dialogRef: MatDialogRef<ChatFilePickerDialogComponent, FileChoice[]>) {}

  attach(): void {
    if (!this.chosen.length) return;
    this.dialogRef.close(this.chosen);
  }

  close(): void {
    this.dialogRef.close();
  }
}
