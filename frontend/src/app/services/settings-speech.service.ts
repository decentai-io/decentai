import { Injectable } from '@angular/core';

import { LlmConnection } from './settings-llm.service';
import { RequestService } from './request.service';

/** With what speech is written down, or a reply said aloud: the
 *  platform's own model, a provider the organization named, or not at
 *  all. */
export type SpeechSource = 'local' | 'connection' | 'off';

/** The organization's choice for each of the two. A connection, its
 *  model and its voice are named only where the source is one. */
export interface SpeechSettings {
  transcription_source: SpeechSource;
  transcription_connection_id: string;
  transcription_model: string;
  speech_source: SpeechSource;
  speech_connection_id: string;
  speech_model: string;
  speech_voice: string;
}

/** Where one of the platform's own models stands on this machine. */
export interface LocalModel {
  state: 'absent' | 'fetching' | 'ready' | 'failed';
  bytes: number;
  of: number;
  error: string;
}

/** The speech container, as the backend found it. */
export interface LocalSpeech {
  reachable: boolean;
  transcription?: LocalModel;
  speech?: LocalModel;
}

/** What a page may offer: a microphone, and a reply said aloud. Each
 *  may be offered while the platform's own model is still on its way. */
export interface SpeechOffered {
  configured: boolean;
  ready: boolean;
  speech: { configured: boolean; ready: boolean };
}

export const NOTHING_OFFERED: SpeechOffered = {
  configured: false, ready: false, speech: { configured: false, ready: false },
};

/** Client for Settings:Speech — the organization's choices, and the two
 *  doors: a recording in and words back, words in and audio back. */
@Injectable({ providedIn: 'root' })
export class SettingsSpeechService {
  constructor(private request: RequestService) {}

  /** One probe for every composer and message on the page, kept for a
   *  short while: they all open at once and ask the same question. */
  private asked: { at: number; answer: Promise<SpeechOffered> } | null = null;

  async get(): Promise<{
    speech: SpeechSettings;
    transcription_connection: LlmConnection | null;
    speech_connection: LlmConnection | null;
    local: LocalSpeech;
  }> {
    const r = await this.request.gateway('Settings:Speech:get');
    return {
      speech: r?.speech,
      transcription_connection: r?.transcription_connection ?? null,
      speech_connection: r?.speech_connection ?? null,
      local: r?.local ?? { reachable: false },
    };
  }

  async update(speech: Partial<SpeechSettings>): Promise<{
    speech?: SpeechSettings; local?: LocalSpeech; error?: string;
  }> {
    this.asked = null;
    return this.request.gateway('Settings:Speech:update', { ...speech });
  }

  /** What this organization offers its pages. */
  offered(fresh = false): Promise<SpeechOffered> {
    const now = Date.now();
    if (!fresh && this.asked && now - this.asked.at < 15000) return this.asked.answer;
    const answer = this.request.gateway('Settings:Speech:transcribe', { probe: true })
      .then((r: any): SpeechOffered => ({
        configured: !!r?.configured,
        ready: !!r?.ready,
        speech: { configured: !!r?.speech?.configured, ready: !!r?.speech?.ready },
      }))
      .catch(() => NOTHING_OFFERED);
    this.asked = { at: now, answer };
    return answer;
  }

  /** Whether a microphone will work at all — so a composer shows one
   *  only when speaking will. */
  async configured(): Promise<boolean> {
    return (await this.offered()).configured;
  }

  /** A recording, as the browser made it, to words. */
  async transcribe(blob: Blob, language?: string): Promise<{ text?: string; error?: string }> {
    const content_base64 = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(',').pop() ?? '');
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(blob);
    });
    return this.request.gateway('Settings:Speech:transcribe', {
      content_base64, mime: blob.type || 'audio/webm', language: language || '',
    });
  }

  /** Words to audio: a file the page plays. */
  async speak(text: string): Promise<{ audio?: Blob; error?: string }> {
    const r = await this.request.gateway('Settings:Speech:speak', { text });
    if (!r?.content_base64) return { error: r?.error || 'Nothing came back to play.' };
    const raw = atob(r.content_base64);
    const bytes = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return { audio: new Blob([bytes], { type: r.mime || 'audio/wav' }) };
  }
}
