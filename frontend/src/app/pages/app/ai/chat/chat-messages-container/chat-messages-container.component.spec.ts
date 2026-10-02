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
