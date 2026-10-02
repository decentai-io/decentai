import { ChatMarkdownService } from './chat-markdown.service';

describe('ChatMarkdownService', () => {
  const clean = (html: string) => ChatMarkdownService.withoutRemoteMedia(html);

  it('turns a picture from elsewhere into a link, and loads nothing', () => {
    const html = clean('<p>See <img src="https://elsewhere.example/p.png?q=secret" alt="the chart"></p>');

    expect(html).not.toContain('<img');
    expect(html).toContain('<a href="https://elsewhere.example/p.png?q=secret">the chart</a>');
  });

  it('shows the address when the picture has no description', () => {
    const html = clean('<img src="https://elsewhere.example/p.png">');

    expect(html).toBe('<a href="https://elsewhere.example/p.png">https://elsewhere.example/p.png</a>');
  });

  it('keeps a picture of the platform itself, and one written out as data', () => {
    const own = `<img src="${window.location.origin}/download/fil_1" alt="mine">`;
    const data = '<img src="data:image/png;base64,AAAA" alt="inline">';

    expect(clean(own)).toContain('<img');
    expect(clean(data)).toContain('<img');
  });

  it('drops the other ways a picture can be named elsewhere', () => {
    const html = clean(
      `<img src="${window.location.origin}/a.png" srcset="https://elsewhere.example/b.png 2x">`
      + '<picture><source srcset="https://elsewhere.example/c.png"><img src="https://elsewhere.example/d.png" alt="d"></picture>'
      + '<video poster="https://elsewhere.example/e.png"></video>');

    expect(html).not.toContain('srcset');
    expect(html).not.toContain('<source');
    expect(html).not.toContain('<video');
    expect(html).not.toContain('d.png"><');
  });

  it('leaves words alone', () => {
    const html = '<p>Nothing to <strong>load</strong> here.</p>';

    expect(clean(html)).toBe(html);
  });
});
