import { NgZone } from '@angular/core';

import { DeviceSpeechService, Hearing } from './device-speech.service';

describe('DeviceSpeechService', () => {
  function create(hearing: Hearing, ownVoice: boolean) {
    const service = new DeviceSpeechService({ run: (fn: () => unknown) => fn() } as NgZone);
    spyOn(service, 'hearing').and.resolveTo(hearing);
    spyOn(service, 'voice').and.resolveTo(ownVoice ? ({ lang: 'en-US' } as SpeechSynthesisVoice) : null);
    return service;
  }

  beforeEach(() => localStorage.removeItem('decentai.speech_here'));
  afterEach(() => localStorage.removeItem('decentai.speech_here'));

  it('writes speech down on the computer when it hears on its own', async () => {
    expect(await create('local', false).dictation(true)).toBe('device');
    expect(await create('local', false).dictation(false)).toBe('device');
  });

  it('never sends a voice to the browser maker unless the person chose it', async () => {
    const service = create('online', false);
    expect(await service.dictation(true)).toBe('server');
    expect(await service.dictation(false)).toBeNull();
    service.choose({ dictation: 'device' });
    expect(await service.dictation(true)).toBe('device');
  });

  it('keeps to the server when the person chose it', async () => {
    const service = create('local', true);
    service.choose({ dictation: 'server', reading: 'server' });
    expect(await service.dictation(true)).toBe('server');
    expect(await service.reading(true)).toBe('server');
    // A server that cannot leaves the computer's own.
    expect(await service.dictation(false)).toBe('device');
    expect(await service.reading(false)).toBe('device');
  });

  it('reads aloud with a voice of the computer’s own, or the server', async () => {
    expect(await create('none', true).reading(true)).toBe('device');
    expect(await create('none', false).reading(true)).toBe('server');
    expect(await create('none', false).reading(false)).toBeNull();
  });
});
