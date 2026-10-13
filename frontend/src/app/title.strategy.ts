import { Injectable } from '@angular/core';
import { Title } from '@angular/platform-browser';
import { RouterStateSnapshot, TitleStrategy } from '@angular/router';

/** What follows a page's name in the browser's tab: "Files · DecentAI". */
export const TITLE_SUFFIX = ' · DecentAI';

/**
 * Names the browser tab after the page: each route carries its `title`
 * (the routes files), and this writes it with the product's name after
 * it. A route with no title is the product's name alone.
 */
@Injectable()
export class PageTitleStrategy extends TitleStrategy {
  constructor(private readonly title: Title) {
    super();
  }

  override updateTitle(snapshot: RouterStateSnapshot): void {
    const page = this.buildTitle(snapshot);
    this.title.setTitle(page ? page + TITLE_SUFFIX : 'DecentAI');
  }
}
