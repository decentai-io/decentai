import { NgIf } from '@angular/common';
import { Component, Input, OnChanges } from '@angular/core';
import { LucideAngularModule } from 'lucide-angular';

import { extensionOf, FILE_KIND_ICONS, fileKindOf } from './file-types';

/**
 * A file's face: a tile with the icon of its kind, told from its name
 * and, failing that, its MIME type. A PDF and an image carry their
 * type as a word under the icon, since their icons alone look like any
 * document's.
 *
 *   <app-file-icon [name]="file.name" [mime]="file.type"></app-file-icon>
 */
@Component({
  selector: 'app-file-icon',
  standalone: true,
  imports: [NgIf, LucideAngularModule],
  styleUrl: './file-icon.component.css',
  host: { 'aria-hidden': 'true' },
  template: `
    <lucide-icon [name]="icon" [size]="word ? 14 : 18" [strokeWidth]="1.75"></lucide-icon>
    <span class="file-icon-word" *ngIf="word">{{ word }}</span>
  `,
})
export class FileIconComponent implements OnChanges {
  @Input({ required: true }) name = '';
  @Input() mime: string | null | undefined = '';

  icon = FILE_KIND_ICONS.file;
  /** The type as a word, for the kinds whose icon does not say it. */
  word = '';

  ngOnChanges(): void {
    const kind = fileKindOf(this.name, this.mime || '');
    this.icon = FILE_KIND_ICONS[kind];
    const extension = extensionOf(this.name);
    this.word = (kind === 'pdf' || kind === 'image') && extension.length <= 4
      ? extension.toUpperCase() : '';
  }
}
