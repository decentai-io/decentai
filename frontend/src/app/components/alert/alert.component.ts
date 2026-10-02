import { Component, Inject } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';

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

  /* Theme-token colors so alerts follow the active (light/dark) M3 theme. */
  getAccentColor(): string {
    const map: Record<string, string> = {
      success: 'var(--ok)',
      error: 'var(--err)',
      confirm: 'var(--warn)',
      info: 'var(--p)',
    };
    return map[this.data?.type] ?? 'var(--osv)';
  }

  getIconBg(): string {
    const map: Record<string, string> = {
      success: 'var(--okc)',
      error: 'var(--errc)',
      confirm: 'var(--warnc)',
      info: 'var(--pc)',
    };
    return map[this.data?.type] ?? 'var(--s3)';
  }

  getHeroBg(): string {
    return 'transparent';
  }

  getTypeIcon(): string {
    const map: Record<string, string> = {
      success: 'circle-check',
      error: 'circle-alert',
      confirm: 'triangle-alert',
      info: 'info',
    };
    return map[this.data?.type] ?? 'bell';
  }

  closeDialogWithData(): void {
    this.dialogRef.close({ confirmed: true });
  }
}
