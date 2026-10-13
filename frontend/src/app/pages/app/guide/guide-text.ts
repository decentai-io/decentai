/**
 * A chapter of the guide as text: its headings, each with the address
 * it can be linked by, and what the search looks through.
 */

/** One chapter, as the index names it. */
export interface GuideChapter {
  id: string;
  title: string;
  summary: string;
  file: string;
  section?: string;
}

/** A heading inside a chapter. */
export interface GuideHeading {
  /** What follows the # in the address. */
  slug: string;
  text: string;
  /** 2 for a section of the chapter, 3 for a part of one. */
  level: number;
}

/** Where the search found the words. */
export interface GuideMatch {
  chapter: GuideChapter;
  /** The heading it is under; null for the chapter's opening. */
  heading: GuideHeading | null;
  /** The sentence the words are in. */
  sentence: string;
}

/** A heading's address: its words, lower case, joined by hyphens. */
export function slugOf(text: string): string {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

/** Markdown marks taken off, so a sentence reads as it is shown. */
function plain(markdown: string): string {
  return markdown
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[*_`>#|]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
}

/** The second- and third-level headings of a chapter, in order. Lines
 *  inside a code fence are not headings. A heading said twice gets a
 *  number after its slug, as the page gives the second one. */
export function headingsOf(markdown: string): GuideHeading[] {
  const headings: GuideHeading[] = [];
  const seen = new Map<string, number>();
  let fenced = false;
  for (const line of markdown.split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
    const found = fenced ? null : /^(#{2,3})\s+(.+?)\s*$/.exec(line);
    if (!found) continue;
    const text = plain(found[2]);
    const base = slugOf(text);
    const count = seen.get(base) || 0;
    seen.set(base, count + 1);
    headings.push({ slug: count ? `${base}-${count + 1}` : base, text, level: found[1].length });
  }
  return headings;
}

/** The sentences of every chapter that hold the words, a few from each
 *  chapter, each under the heading it was found below. */
export function searchGuide(
  query: string, chapters: GuideChapter[], texts: Record<string, string>, perChapter = 3,
): GuideMatch[] {
  const words = query.trim().toLowerCase();
  if (words.length < 2) return [];
  const matches: GuideMatch[] = [];
  for (const chapter of chapters) {
    const headings = headingsOf(texts[chapter.id] || '');
    let heading: GuideHeading | null = null;
    let next = 0;
    let found = 0;
    let fenced = false;
    if (`${chapter.title} ${chapter.summary}`.toLowerCase().includes(words)) {
      matches.push({ chapter, heading: null, sentence: chapter.summary });
      found += 1;
    }
    for (const line of (texts[chapter.id] || '').split('\n')) {
      if (found >= perChapter) break;
      if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
      if (fenced) continue;
      if (/^#{2,3}\s/.test(line)) { heading = headings[next] || null; next += 1; continue; }
      if (/^#\s/.test(line) || !line.toLowerCase().includes(words)) continue;
      const sentence = plain(line).split(/(?<=[.!?])\s+/).find((each) => each.toLowerCase().includes(words));
      if (!sentence) continue;
      matches.push({ chapter, heading, sentence });
      found += 1;
    }
  }
  return matches;
}
