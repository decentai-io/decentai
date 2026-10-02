import { Injectable } from '@angular/core';
import { environment } from 'src/environments/environment';
import { loginUrl } from './auth.util';

@Injectable({
  providedIn: 'root',
})
export class RequestService {
  endpoint: string = environment.apiEndpoint; // e.g., "http://127.0.0.1/"

  /**
   * Generic request sender. The session cookie rides on every call.
   */
  async sendRequest(
    body: any,
    headers: any = null,
    path: string = 'app',
    method: string = 'POST',
    isFileUpload = false,
  ): Promise<any> {
    try {
      const url = `${this.endpoint}${path}`;

      // Setup headers
      if (isFileUpload) {
        headers = {}; // Let browser set boundary
      } else if (!headers) {
        headers = { 'Content-Type': 'application/json' };
      }

      // Build request
      const options: RequestInit = {
        method: method,
        credentials: 'include', // the session cookie rides on this
        headers,
      };

      // Add body only for POST/PUT
      if (method !== 'GET' && body) {
        options.body = isFileUpload ? body : JSON.stringify(body);
      }

      // Send request
      const response = await fetch(url, options);
      const status = response.status;

      // Handle unauthorized
      if (status === 401) {
        window.location.href = loginUrl();
        return { status, data: { message: 'Unauthorized' } };
      }

      // Handle HTML responses (like redirects or errors)
      const text = await response.text();
      let data;

      try {
        data = JSON.parse(text);
      } catch {
        data = text;
      }

      return { status, data };
    } catch {
      // The request never got an answer. Status 0 keeps that apart from
      // a server that answered with a failure of its own.
      return {
        status: 0,
        data: { error: 'Could not reach the server. Check your connection.' },
      };
    }
  }

  /**
   * One gateway call: POST /app {endpoint, data} → the response body, or
   * {error} with the human-readable reason (and whatever extra fields the
   * backend attached — e.g. `ungrantable` on a rejected policy grant).
   *
   * Every IAM service goes through here so error unwrapping exists once.
   */
  async gateway<T = any>(
    endpoint: string,
    data: any = {},
  ): Promise<T & { error?: string }> {
    const res = await this.sendRequest({ endpoint, data });
    if (res.status !== 200) {
      const error =
        res?.data?.error ||
        res?.data?.messages?.[0]?.description ||
        `Request failed (${res.status}).`;
      return {
        ...(typeof res.data === 'object' ? res.data : {}),
        error,
      } as T & { error?: string };
    }
    return res.data as T & { error?: string };
  }
}
