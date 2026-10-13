/**
 * Markdown that somebody else wrote (a package's README, a file) is
 * shown inside a page that has its own title. Its headings are moved
 * down so the page keeps its one `h1` and its outline: a "# Title"
 * shown two levels down is an `h3`. Lines inside a code fence are left
 * as they are, and nothing goes below `h6`.
 */
export function headingsDown(markdown: string, levels: number): string {
  let fenced = false;
  return markdown.split('\n').map((line) => {
    if (/^\s*(```|~~~)/.test(line)) fenced = !fenced;
    if (fenced) return line;
    return line.replace(/^(#{1,6})(?=\s)/, (marks) => '#'.repeat(Math.min(6, marks.length + levels)));
  }).join('\n');
}
