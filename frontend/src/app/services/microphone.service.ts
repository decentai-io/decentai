import { Injectable } from '@angular/core';

/** The microphone, asked for once per app session and kept.
 *
 *  iPhone and iPad ask the person again on every navigation when a
 *  page starts a fresh capture each time, and a chat is a navigation.
 *  Kept, the stream is muted between recordings rather than stopped,
 *  so the next recording reuses the grant. A stream the device ended
 *  in the background is asked for again, which is the prompt the
 *  person would have seen anyway. */
@Injectable({ providedIn: 'root' })
export class MicrophoneService {
  private stream: MediaStream | null = null;

  /** Whether this browser can record at all. */
  get available(): boolean {
    return typeof MediaRecorder !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;
  }

  /** A live, unmuted stream: the kept one, or a new grant. */
  async acquire(): Promise<MediaStream> {
    if (this.stream && this.stream.getAudioTracks().some((track) => track.readyState === 'live')) {
      this.stream.getAudioTracks().forEach((track) => (track.enabled = true));
      return this.stream;
    }
    this.dispose();
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    return this.stream;
  }

  /** Between recordings: muted, not ended, so no new prompt. */
  release(): void {
    this.stream?.getAudioTracks().forEach((track) => (track.enabled = false));
  }

  /** Given back for good, on sign-out. */
  dispose(): void {
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
  }
}
