import { Injectable } from '@angular/core';
import { MarkdownService, ParseOptions } from 'ngx-markdown';
import { environment } from 'src/environments/environment';

/**
 * Markdown as a chat message shows it: nothing in it is fetched from
 * somewhere else.
 *
 * What a message says may come from a web page, a document or a tool an
 * agent read — text nobody here wrote. A picture in it is requested the
 * moment it is drawn, with no click, and its address can carry whatever
 * the text around it was told to put there. So a picture, a video or a
 * sound whose address is not this platform's own is not loaded: the
 * picture becomes a link the person may choose to follow, showing its
 * description or its address.
 *
 * The chat message provides this in place of the app's MarkdownService,
 * so only messages are rendered this way. It works on the HTML the
 * parent has already sanitized, in a document that loads nothing.
 */
@Injectable()
export class ChatMarkdownService extends MarkdownService {
  override async parse(markdown: string, parseOptions?: ParseOptions): Promise<string> {
    return ChatMarkdownService.withoutRemoteMedia(
      await super.parse(markdown, parseOptions));
  }

  /** The same HTML with everything that would load from elsewhere
   *  taken out. */
  static withoutRemoteMedia(html: string): string {
    if (!html || !/<(img|picture|source|video|audio|track)\b/i.test(html)) return html;
    const page = new DOMParser().parseFromString(`<body>${html}</body>`, 'text/html');
    const linked = (address: string, words: string): HTMLElement => {
      const link = page.createElement('a');
      link.setAttribute('href', address);
      link.textContent = words || address || 'image';
      return link;
    };
    page.body.querySelectorAll('img').forEach((image) => {
      const address = image.getAttribute('src') || '';
      // A list of candidate addresses is a second way to name a picture
      // elsewhere; a chat message has no use for one.
      image.removeAttribute('srcset');
      if (ChatMarkdownService.own(address)) return;
      image.replaceWith(linked(address, image.getAttribute('alt') || ''));
    });
    page.body.querySelectorAll('video, audio').forEach((media) => {
      const address = media.getAttribute('src') || '';
      const poster = media.getAttribute('poster') || '';
      const sources = Array.from(media.querySelectorAll('source, track'))
        .map((source) => source.getAttribute('src') || '');
      const elsewhere = [address, poster, ...sources]
        .filter((value) => value && !ChatMarkdownService.own(value));
      if (elsewhere.length) media.replaceWith(linked(elsewhere[0], ''));
    });
    // A picture's alternatives, and any source left outside a player.
    page.body.querySelectorAll('source, track').forEach((source) => {
      const address = source.getAttribute('src') || '';
      if (source.hasAttribute('srcset') || (address && !ChatMarkdownService.own(address))) {
        source.remove();
      }
    });
    return page.body.innerHTML;
  }

  /** Whether an address is this platform's own: the page's origin, the
   *  API's, or the picture itself written out as data. */
  private static own(address: string): boolean {
    const value = String(address || '').trim();
    if (!value) return false;
    if (/^data:image\//i.test(value)) return true;
    try {
      const at = new URL(value, document.baseURI);
      if (at.protocol !== 'http:' && at.protocol !== 'https:') return false;
      const api = new URL(environment.apiEndpoint, window.location.origin);
      return at.origin === window.location.origin || at.origin === api.origin;
    } catch {
      return false;
    }
  }
}
