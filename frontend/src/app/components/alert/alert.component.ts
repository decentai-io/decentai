import { Component, Inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';

/**
 * A message in a dialog: a title, sentences, and tables of what they
 * are about. With `type: 'confirm'` it asks, and closes with
 * `{ confirmed: true }` when the person goes ahead; `confirm` names
 * that button and `danger` makes it the red one. A plain question
 * with no table is asked through ConfirmService instead.
 */
@Component({
  selector: 'app-alert',
  templateUrl: './alert.component.html',
  styleUrls: ['./alert.component.css'],
  standalone: false,
})
export class AlertComponent {
  constructor(
    @Inject(MAT_DIALOG_DATA) public data: any,
    private dialogRef: MatDialogRef<any>,
  ) {}

  getMessages(): any[] {
    return Array.isArray(this.data?.messages) ? this.data.messages : [];
  }

  getTableRows(table: any): any[] {
    return Array.isArray(table?.rows) ? table.rows : [];
  }

  getTableCols(table: any): string[] {
    if (Array.isArray(table?.cols) && table.cols.length > 0) {
      return table.cols;
    }

    const firstRow = this.getTableRows(table)[0];
    return firstRow && typeof firstRow === 'object' ? Object.keys(firstRow) : [];
  }

  hasTable(table: any): boolean {
    return this.getTableRows(table).length > 0 && this.getTableCols(table).length > 0;
  }

  hasTables(): boolean {
    return this.getMessages().some((message) => this.hasTable(message?.table));
  }

  formatCell(value: any): string {
    if (value === null || value === undefined || value === '') {
      return '-';
    }

    if (typeof value === 'object') {
      return JSON.stringify(value);
    }

    return String(value);
  }

  closeDialogWithData(): void {
    this.dialogRef.close({ confirmed: true });
  }
}
