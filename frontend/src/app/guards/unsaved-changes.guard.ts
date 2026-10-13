import { inject } from '@angular/core';
import { CanDeactivateFn } from '@angular/router';
import { ConfirmService } from '../services/confirm.service';

/** A page with a form says whether leaving it would lose something. */
export interface HasUnsavedChanges {
  /** The page's name when it has changes not yet saved, else ''. */
  unsavedChangesTo(): string;
}

/**
 * Leaving a page with unsaved changes asks first. On every page with a
 * save bar (the routes files name it as `canDeactivate`); the sidebar
 * and the settings nav both go through the router, so both are seen.
 */
export const unsavedChangesGuard: CanDeactivateFn<Partial<HasUnsavedChanges>> = (page) => {
  const name = page.unsavedChangesTo?.() ?? '';
  if (!name) return true;
  return inject(ConfirmService).ask({
    title: `Discard the changes to ${name}?`,
    confirm: 'Discard',
    cancel: 'Keep editing',
    danger: true,
  });
};
