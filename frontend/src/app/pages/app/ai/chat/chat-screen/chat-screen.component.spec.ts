import { ChatScreenComponent, ScreenView, screenLog } from './chat-screen.component';

describe('the live view’s log', () => {
  it('keeps the last five lines, oldest first', () => {
    let log: string[] = [];
    for (const line of ['one', 'two', 'three', 'four', 'five', 'six']) log = screenLog(log, line);

    expect(log).toEqual(['two', 'three', 'four', 'five', 'six']);
  });

  it('keeps a line said twice running once, and nothing for no words', () => {
    const log = screenLog(screenLog(['Opening https://example.com'], 'Opening https://example.com'), '  ');

    expect(log).toEqual(['Opening https://example.com']);
  });
});

describe('ChatScreenComponent, the address box', () => {
  function frame(address: string): ScreenView {
    return {
      call_id: 'c_1', src: '', width: 1, height: 1, frame: 1, at: 0,
      tabs: [
        { index: 1, title: 'Other', address: 'https://other.example', active: false },
        { index: 2, title: 'Here', address, active: true },
      ],
    };
  }

  function show(component: ChatScreenComponent, view: ScreenView): void {
    component.frame = view;
    component.ngOnChanges({ frame: {} as any });
  }

  it('says where the browser is, and follows it', () => {
    const component = new ChatScreenComponent();
    show(component, frame('https://example.com/a'));
    expect(component.address).toBe('https://example.com/a');

    show(component, frame('https://example.com/b'));
    expect(component.address).toBe('https://example.com/b');
    component.ngOnDestroy();
  });

  it('leaves what is being typed alone, and sends the browser there', () => {
    const component = new ChatScreenComponent();
    const sent: any[] = [];
    component.screenInput.subscribe((events) => sent.push(...events));
    show(component, frame('https://example.com/a'));

    component.typeAddress('decentai.org');
    show(component, frame('https://example.com/b'));
    expect(component.address).toBe('decentai.org');

    component.go();
    expect(sent).toEqual([{ type: 'navigate', url: 'https://decentai.org' }]);
    show(component, frame('https://decentai.org/'));
    expect(component.address).toBe('https://decentai.org/');
    component.ngOnDestroy();
  });
});
