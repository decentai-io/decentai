import { Component, NO_ERRORS_SCHEMA } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { HttpClientModule } from '@angular/common/http';
import { Clipboard } from '@angular/cdk/clipboard';
import { MarkdownModule } from 'ngx-markdown';

import { ChatMessageComponent } from './chat-message.component';
import { AiSessionService } from 'src/app/services/ai-session.service';
import { ReadAloudService } from 'src/app/services/read-aloud.service';

const words = (n: number) =>
  Array.from({ length: n }, (_, i) => `Paragraph ${i} ` + 'words '.repeat(40)).join('\n\n');
/** Long enough to be folded, with a code block in the middle and the
 *  answer's own words after it. */
const LONG = words(30) + '\n\n```python\nx = "' + 'a long line with spaces '.repeat(20) +
  '"\n```\n\nAFTER THE BLOCK ' + words(8);

const answer = (text: string, id = 'm1') =>
  ({ actor: 'ai', message_id: id, parts: [{ type: 'markdown', text }] });

@Component({
  standalone: false,
  template: `<div style="width: 600px">
    <app-chat-message [message]="message" [streaming]="streaming"></app-chat-message></div>`,
})
class Host {
  message: any = answer(LONG);
  streaming = false;
}

describe('ChatMessageComponent: a code block and the fold', () => {
  let fixture: ComponentFixture<Host>;
  let root: HTMLElement;

  const settle = async () => {
    fixture.detectChanges();
    await fixture.whenStable();
    await new Promise((resolve) => setTimeout(resolve, 50));
    fixture.detectChanges();
  };
  const pre = () => root.querySelector('pre') as HTMLElement;
  const wrapButton = () => Array.from(root.querySelectorAll<HTMLElement>('pre .code-toolbar button'))
    .find((button) => button.textContent === 'Wrap') as HTMLElement;
  const fold = () => root.querySelector('.msg-fold button') as HTMLElement;
  const folded = () => root.querySelector('.ai-content')!.classList.contains('is-folded');

  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [HttpClientModule, MarkdownModule.forRoot()],
      declarations: [Host, ChatMessageComponent],
      providers: [
        { provide: Clipboard, useValue: { copy: () => true } },
        { provide: AiSessionService, useValue: {} },
        { provide: ReadAloudService, useValue: { speaking: '', problemAt: '' } },
      ],
      schemas: [NO_ERRORS_SCHEMA],
    }).compileComponents();
    fixture = TestBed.createComponent(Host);
    root = fixture.nativeElement;
    document.body.appendChild(root);
    await settle();
  });

  afterEach(() => root.remove());

  it('wraps a block, and keeps it wrapped when the answer is drawn again', async () => {
    fold().click();
    await settle();
    wrapButton().click();
    await settle();
    expect(pre().classList).toContain('code-wrap');
    expect(pre().scrollWidth).toBeLessThanOrEqual(pre().clientWidth);

    // More words arrive: the markdown is drawn again, as new elements.
    const before = pre();
    fixture.componentInstance.message = answer(LONG + ' And one more sentence.');
    await settle();
    expect(pre()).not.toBe(before);
    expect(pre().classList).toContain('code-wrap');
    expect(wrapButton().getAttribute('aria-pressed')).toBe('true');
  });

  it('keeps an opened answer open when the same message is handed in anew', async () => {
    expect(folded()).toBeTrue();
    fold().click();
    await settle();
    expect(folded()).toBeFalse();

    fixture.componentInstance.message = answer(LONG);  // the thread refreshed
    fixture.componentInstance.streaming = true;        // and another input moved
    await settle();
    expect(folded()).toBeFalse();
    expect(fold().textContent).toContain('Show less');

    fixture.componentInstance.message = answer(LONG, 'm2');  // another message
    await settle();
    expect(folded()).toBeTrue();
  });

  it('keeps the fold in sight when the answer is folded again', async () => {
    fold().click();
    await settle();
    const scrolled = spyOn(Element.prototype, 'scrollIntoView');
    fold().click();
    await settle();
    expect(folded()).toBeTrue();
    expect(scrolled).toHaveBeenCalledWith({ block: 'nearest' });
    expect(scrolled.calls.mostRecent().object as unknown).toBe(root.querySelector('.msg-fold'));
  });
});
