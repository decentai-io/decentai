import { Injectable } from '@angular/core';
import { environment } from 'src/environments/environment';

import { RequestService } from './request.service';

export interface FileOwner {
  groups: string[];
  users: string[];
}

/** Backend-owned file metadata — callers never write these. */
export interface FileValues {
  filename?: string;
  file_type?: string;
  file_size?: number;
  folder?: string;
  storage_provider?: string;
}

export interface FileResource {
  resource_ref: string;
  /** The storage id the bytes live under, not a label. */
  resource_id: string;
  owner: FileOwner;
  /** Caller metadata: a chat attachment carries its chat_id here; an
   *  agent's file carries its category, `<ref>__<resource id>`. */
  keys: Record<string, any>;
  values: FileValues;
  /** The agent that stored it, named by the backend from the category
   *  key; absent for an upload or a chat attachment. The name is empty
   *  when no approval answers for the ref any more. */
  agent?: { agent_id: string; name: string } | null;
  /** Its metadata is encrypted under a key version this deployment no
   *  longer holds: the row is real, its details cannot be read. */
  unreadable?: boolean;
  created_by: string;
  created_at?: string | null;
  updated_at?: string | null;
}

/**
 * Client for Files:File — every file the user can see, whatever put it
 * there: a chat attachment, an agent's output, a direct upload. Metadata
 * comes back on the gateway; the bytes come from /download, which streams
 * them with the session cookie.
 */
@Injectable({ providedIn: 'root' })
export class FilesService {
  constructor(private request: RequestService) {}

  async list(): Promise<FileResource[]> {
    const result = await this.request.gateway('Files:File:list', {});
    return Array.isArray(result?.resources) ? result.resources : [];
  }

  /** Hand a file to another member; its sharing stays. */
  async transfer(ref: string, userId: string): Promise<{ resource?: any; error?: string }> {
    return this.request.gateway('Files:File:transfer', { resource_ref: ref, user_id: userId });
  }

  async remove(ref: string): Promise<{ deleted?: boolean; error?: string }> {
    return this.request.gateway('Files:File:delete', { resource_ref: ref });
  }

  /** Upload one file from the page. The same door a chat attachment and
   *  the runtime use — base64 over the gateway, the backend owns the
   *  metadata it writes. */
  async upload(
    file: File,
  ): Promise<{ resource?: FileResource; error?: string }> {
    const content_base64 = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () =>
        resolve(String(reader.result).split(',').pop() ?? '');
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
    return this.request.gateway('Files:File:upload', {
      filename: file.name,
      content_base64,
    });
  }

  /** Change who can see a file. Only the owner map travels — the rest of
   *  a file's metadata is the platform's, written when the bytes landed. */
  async setOwner(
    ref: string,
    owner: FileOwner,
  ): Promise<{ resource?: FileResource; error?: string }> {
    return this.request.gateway('Files:File:update', {
      resource_ref: ref,
      owner,
    });
  }

  /** Direct download URL; the browser's GET carries the session cookie. */
  downloadUrl(ref: string): string {
    return new URL(
      `download/${encodeURIComponent(ref)}`,
      new URL(environment.apiEndpoint, window.location.origin),
    ).toString();
  }
}
