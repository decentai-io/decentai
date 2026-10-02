import { Component, Input, OnChanges, SimpleChanges, ViewChild } from '@angular/core';
import { MatPaginator } from '@angular/material/paginator';
import { MatSort } from '@angular/material/sort';
import { MatTableDataSource } from '@angular/material/table';

/** A read-only table of rows: sortable, paged, downloadable as CSV. */
@Component({
    selector: 'app-informative-table',
    templateUrl: './informative-table.component.html',
    styleUrls: ['./informative-table.component.css'],
    standalone: false
})
export class InformativeTableComponent implements OnChanges {
  @Input() title: string = 'Informative Table';
  @Input() data: any[] = [];
  @Input() cols: string[] = [];

  /** The table alone: no download button, no paging. */
  @Input() justTable = false;

  readonly dataSource = new MatTableDataSource<any>([]);

  // The table is only rendered once there are rows, so the sort and the
  // paginator can appear after the data has: each is attached when it
  // arrives, and neither waits for the other.
  @ViewChild(MatSort) set sort(sort: MatSort | undefined) {
    this.dataSource.sort = sort ?? null;
  }

  @ViewChild(MatPaginator) set paginator(paginator: MatPaginator | undefined) {
    this.dataSource.paginator = paginator ?? null;
  }

  get rows(): any[] {
    return Array.isArray(this.data) ? this.data : [];
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['data']) {
      this.dataSource.data = this.rows;
    }
  }

  download(): void {
    const cols = this.cols ?? [];
    const lines = [cols.map((col) => this.csvCell(col)).join(',')];
    for (const row of this.rows) {
      lines.push(cols.map((col) => this.csvCell(row?.[col])).join(','));
    }

    // A byte-order mark so a spreadsheet reads it as UTF-8, and a Blob
    // rather than a data: address, which stops at the first '#'.
    const blob = new Blob(['\uFEFF' + lines.join('\r\n')], {
      type: 'text/csv;charset=utf-8;',
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `${this.fileName()}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  private fileName(): string {
    return (
      String(this.title ?? '')
        .trim()
        .replace(/[^\w\-]+/g, '_')
        .replace(/^_+|_+$/g, '') || 'export'
    );
  }

  private csvCell(value: any): string {
    if (value === null || value === undefined) {
      return '';
    }

    let cell =
      typeof value === 'object' ? JSON.stringify(value) : String(value).trim();

    // A cell that starts like a formula would run as one when the file is
    // opened in a spreadsheet; the apostrophe makes it text.
    if (/^[=+\-@\t\r]/.test(cell)) {
      cell = `'${cell}`;
    }

    if (/[",\n\r]/.test(cell)) {
      cell = `"${cell.replace(/"/g, '""')}"`;
    }

    return cell;
  }
}
