import { ChatMessagesContainerComponent } from './chat-messages-container.component';

/** A scroll container that behaves like the browser's: a programmatic
 *  scroll raises scroll events of its own, which is the whole reason the
 *  thread used to stop short of its end. */
class FakeScroller {
  scrollTop = 0;
  clientHeight = 800;
  scrollHeight = 5000;
  /** Positions to report while a scroll we started is under way. */
  frames: number[] = [];
  private onScroll: () => void = () => {};

  watch(handler: () => void) {
    this.onScroll = handler;
  }

  scrollTo(options: { top: number }) {
    // The frames the animation passes through before it lands.
    for (const top of this.frames) {
      this.scrollTop = top;
      this.onScroll();
    }
    this.scrollTop = options.top;
    this.onScroll();
  }
}

describe('ChatMessagesContainerComponent following the foot of the thread', () => {
  function containerWith(el: FakeScroller) {
    const ngZone = {
      runOutsideAngular: (fn: () => void) => fn(),
      run: (fn: () => void) => fn(),
    };
    const component = new ChatMessagesContainerComponent(ngZone as any);
    (component as any).messagesContainer = { nativeElement: el };
    el.watch(() => component.onScroll());
    return component;
  }

  it('keeps following when the scroll events come from our own move', () => {
    const el = new FakeScroller();
    // Half way down, a frame of our own animation looks exactly like a
    // person who has scrolled away from the end.
    el.frames = [1200, 2600];
    const component = containerWith(el);

    (component as any).scrollToBottom(true);

    expect((component as any).userNearBottom).toBe(true);
  });

  it('stops following when the person scrolls away themselves', () => {
    const el = new FakeScroller();
    const component = containerWith(el);

    el.scrollTop = 2000;
    component.onScroll();

    expect((component as any).userNearBottom).toBe(false);
  });

  it('chases content that arrives after the move, instead of landing short', () => {
    const el = new FakeScroller();
    const component = containerWith(el);

    (component as any).scrollToBottom(false);
    expect(el.scrollTop).toBe(5000);

    // A stored table hydrates and the thread grows underneath us. This is
    // what the resize observer does, and it only does it while the thread
    // is still considered followed.
    el.scrollHeight = 5600;
    if ((component as any).userNearBottom) (component as any).scrollToBottom(false);

    expect(el.scrollTop).toBe(5600);
  });

  it('asks for an instant move when it is not meant to glide', () => {
    const el = new FakeScroller();
    const component = containerWith(el);
    const asked: string[] = [];
    (el as any).scrollTo = (options: { top: number; behavior: string }) => {
      asked.push(options.behavior);
      el.scrollTop = options.top;
    };

    (component as any).scrollToBottom(false);
    (component as any).scrollToBottom(true);

    expect(asked).toEqual(['auto', 'smooth']);
  });
});

describe('ChatMessagesContainerComponent, a turn’s work', () => {
  function container() {
    return new ChatMessagesContainerComponent({ runOutsideAngular: (fn: () => void) => fn() } as any);
  }

  it('says what a finished turn’s work came to', () => {
    const line = container().ranLine([
      { id: 'c_1', text: 'Notebook · Save Note', agent: 'Notebook', status: 'success', durationMs: 1800 },
      { id: 'c_2', text: 'Browser · Open', agent: 'Browser', status: 'error', durationMs: 40200 },
      { id: 'note_1', text: 'Stopped.' },
    ]);

    expect(line).toBe('Ran 3 steps · 2 agents · 1 failed · 42s');
  });

  it('opens the turn in hand and an earlier turn apart', () => {
    const component = container();

    component.toggleWork('turn_1', false);
    expect(component.isWorkOpen('turn_1', false)).toBeTrue();
    expect(component.isWorkOpen('turn_2', true)).toBeFalse();

    component.toggleWork('turn_2', true);
    component.toggleWork('turn_1', false);
    expect(component.isWorkOpen('turn_2', true)).toBeTrue();
    expect(component.isWorkOpen('turn_1', false)).toBeFalse();
  });

  it('has a block for the turn in hand while it runs, and none for a chat just opened', () => {
    const component = container();
    expect(component.showLiveWork).toBeFalse();

    component.isProcessing = true;
    expect(component.showLiveWork).toBeTrue();
  });
});
