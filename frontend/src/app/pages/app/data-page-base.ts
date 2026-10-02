/**
 * Shared notice/error state for the resource pages — files, secrets, and
 * the IAM console. Each shows the same transient "saved" flash and sticky
 * error banner; this base carries that plumbing so it isn't repeated per
 * page.
 */
export abstract class DataPageBase {
  /** Transient success message; clears itself after a few seconds. */
  notice = '';
  /** Sticky error message; stays until the next action succeeds. */
  error = '';

  protected flash(message: string): void {
    this.notice = message;
    this.error = '';
    setTimeout(() => (this.notice = ''), 3500);
  }

  protected fail(message: string): void {
    this.error = message;
    this.notice = '';
  }
}
