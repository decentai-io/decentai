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
  /** Who stored it, by name; empty when that person is gone. */
  created_by_name?: string;
  /** The names of `owner.groups` and `owner.users`, in their order. */
  owner_names?: { groups: string[]; users: string[] };
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

  /** The largest file the upload door takes, in MB, as the last list
   *  said it; the backend's default until one has. */
  maxUploadMb = 25;

  async list(): Promise<FileResource[]> {
    const result = await this.request.gateway('Files:File:list', {});
    if (Number(result?.max_upload_mb) > 0) this.maxUploadMb = Number(result.max_upload_mb);
    return Array.isArray(result?.resources) ? result.resources : [];
  }

  /** Hand a file to another member; its sharing stays. */
  async transfer(ref: string, userId: string): Promise<{ resource?: any; error?: string }> {
    return this.request.gateway('Files:File:transfer', { resource_ref: ref, user_id: userId });
  }

  async remove(ref: string): Promise<{ deleted?: boolean; error?: string }> {
    return this.request.gateway('Files:File:delete', { resource_ref: ref });
  }

  /** Upload one file from the page: the bytes as they are, in a
   *  multipart body, to the door a chat attachment uses. `progress` is
   *  told how much has been sent, from 0 to 1; `cancel` stops it, and
   *  the answer then says so. */
  upload(file: File, progress?: (sent: number) => void): {
    done: Promise<{ resource?: FileResource; error?: string }>;
    cancel: () => void;
  } {
    const call = new XMLHttpRequest();
    const done = new Promise<{ resource?: FileResource; error?: string }>((resolve) => {
      const answer = (): any => {
        try { return JSON.parse(call.responseText || '{}'); } catch { return {}; }
      };
      call.upload.onprogress = (event) => {
        if (event.lengthComputable) progress?.(event.loaded / event.total);
      };
      call.onload = () => {
        const body = answer();
        resolve(call.status === 200 && body.resource
          ? { resource: body.resource }
          : { error: body.error || `The upload was refused (${call.status}).` });
      };
      call.onerror = () => resolve({ error: 'The connection was lost before the file arrived.' });
      call.onabort = () => resolve({ error: 'Cancelled.' });
    });

    const body = new FormData();
    body.append('file', file, file.name);
    body.append('additionalData', JSON.stringify({ endpoint: 'Files:File:Upload' }));
    call.open('POST', new URL('upload', new URL(environment.apiEndpoint, window.location.origin)).toString());
    call.withCredentials = true; // the session cookie rides on this
    call.send(body);
    return { done, cancel: () => call.abort() };
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

  /** A new name for a file. Its extension stays: the backend refuses a
   *  name that would say another type. */
  async rename(ref: string, filename: string): Promise<{ resource?: FileResource; error?: string }> {
    return this.request.gateway('Files:File:update', { resource_ref: ref, filename });
  }

  /** Direct download URL; the browser's GET carries the session cookie. */
  downloadUrl(ref: string): string {
    return new URL(
      `download/${encodeURIComponent(ref)}`,
      new URL(environment.apiEndpoint, window.location.origin),
    ).toString();
  }
}
