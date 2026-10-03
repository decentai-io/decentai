import { Injectable } from '@angular/core';

import { LlmConnection } from './settings-llm.service';
import { RequestService } from './request.service';

/** Which model writes spoken messages down: a connection shared with
 *  everyone, and one of its provider's transcription models. */
export interface SpeechSettings {
  /** '' for none: the composer offers no microphone. */
  transcription_connection_id: string;
  transcription_model: string;
}

/** Client for Settings:Speech — the organization's transcription model,
 *  and the door a recording goes through to come back as words. */
@Injectable({ providedIn: 'root' })
export class SettingsSpeechService {
  constructor(private request: RequestService) {}

  async get(): Promise<{
    speech: SpeechSettings; transcription_connection: LlmConnection | null;
  }> {
    const r = await this.request.gateway('Settings:Speech:get');
    return { speech: r?.speech, transcription_connection: r?.transcription_connection ?? null };
  }

  async update(speech: SpeechSettings): Promise<{
    speech?: SpeechSettings; error?: string;
  }> {
    return this.request.gateway('Settings:Speech:update', { ...speech });
  }

  /** Whether a transcription model is configured at all — asked once,
   *  so a composer shows its microphone only when speaking will work. */
  async configured(): Promise<boolean> {
    try {
      const r = await this.request.gateway('Settings:Speech:transcribe', { probe: true });
      return !!r?.configured;
    } catch {
      return false;
    }
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
}
