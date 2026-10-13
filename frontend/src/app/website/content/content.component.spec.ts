import { NO_ERRORS_SCHEMA } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Title } from '@angular/platform-browser';
import { NavigationEnd, NavigationStart, Router } from '@angular/router';
import { Subject } from 'rxjs';
import { ContentComponent } from './content.component';

describe('ContentComponent', () => {
  let events: Subject<unknown>;
  let fixture: ComponentFixture<ContentComponent>;
  let container: HTMLElement;
  let page: HTMLElement;
  let id: number;

  /** One frame of the browser, which is when a resize is noticed. */
  const frame = () => new Promise<void>((done) => requestAnimationFrame(() => done()));

  /** A navigation from start to end; `back` names the navigation it
   *  returns to, as Back and Forward do. */
  function navigate(url: string, back?: number): number {
    id += 1;
    events.next(
      back === undefined
        ? new NavigationStart(id, url)
        : new NavigationStart(id, url, 'popstate', { navigationId: back }),
    );
    events.next(new NavigationEnd(id, url, url));
    return id;
  }

  beforeEach(() => {
    events = new Subject();
    id = 0;
    TestBed.configureTestingModule({
      declarations: [ContentComponent],
      providers: [
        { provide: Router, useValue: { events } },
        { provide: Title, useValue: { getTitle: () => 'Files · DecentAI' } },
      ],
      schemas: [NO_ERRORS_SCHEMA],
    });
    fixture = TestBed.createComponent(ContentComponent);
    fixture.detectChanges();

    container = fixture.nativeElement.querySelector('#page_container');
    container.style.cssText = 'display:block; height:200px; overflow-y:auto;';
    page = fixture.nativeElement.querySelector('.content');
    page.style.cssText = 'display:block; padding:0; animation:none; height:2000px;';
    page.insertAdjacentHTML('beforeend', '<h1>Files</h1>');
  });

  it('opens a new page at its top', () => {
    navigate('/guide');
    fixture.detectChanges();
    container.scrollTop = 500;

    navigate('/data/files');
    fixture.detectChanges();

    expect(container.scrollTop).toBe(0);
  });

  it('returns to where the person was on Back, once the page is tall enough', async () => {
    const guide = navigate('/guide');
    fixture.detectChanges();
    container.scrollTop = 500;
    navigate('/data/files');
    fixture.detectChanges();

    // Back: the page being returned to has not loaded its text yet.
    page.style.height = '50px';
    navigate('/guide', guide);
    fixture.detectChanges();
    expect(container.scrollTop).toBe(0);

    page.style.height = '2000px';
    await frame();
    await frame();

    expect(container.scrollTop).toBe(500);
  });

  it('moves the focus to the title and says the page\'s name when the page changes', () => {
    navigate('/guide');
    fixture.detectChanges();
    const announcer: HTMLElement = fixture.nativeElement.querySelector('[aria-live]');
    // The first page is where the person arrived, not a change.
    expect(announcer.textContent).toBe('');

    navigate('/data/files');
    fixture.detectChanges();

    expect(document.activeElement).toBe(page.querySelector('h1'));
    expect(announcer.textContent).toBe('Files');
  });

  it('leaves the focus alone when only the query changes', () => {
    navigate('/agents/marketplace');
    fixture.detectChanges();
    const before = document.activeElement;

    navigate('/agents/marketplace?review=jira');
    fixture.detectChanges();

    expect(document.activeElement).toBe(before);
  });
});
