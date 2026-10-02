import { Component, Inject, OnInit } from '@angular/core';
import { MAT_DIALOG_DATA, MatDialogRef } from '@angular/material/dialog';
import { AiSessionService } from 'src/app/services/ai-session.service';

/**
 * Which language model this chat thinks with. The chat only ever picks
 * an LLM connection the person can already see (Settings → Language
 * models); provider and model come from that connection's metadata,
 * and the API key itself stays encrypted in the platform — it never
 * reaches this browser.
 */
@Component({
  selector: 'app-chat-llm-dialog',
  standalone: false,
  templateUrl: './chat-llm-dialog.component.html',
  styleUrls: ['./chat-llm-dialog.component.css'],
})
export class ChatLlmDialogComponent implements OnInit {
  secretRef = '';
  secrets: any[] = [];
  isLoading = true;

  constructor(
    @Inject(MAT_DIALOG_DATA) public data: any,
    private dialogRef: MatDialogRef<ChatLlmDialogComponent>,
    private aiSession: AiSessionService,
  ) {
    this.secretRef = data?.llm?.secret_ref || '';
  }

  async ngOnInit(): Promise<void> {
    this.secrets = await this.aiSession.listLlmSecrets();
    this.isLoading = false;

    // A sole visible key preselects itself.
    if (!this.secretRef && this.secrets.length === 1) {
      this.secretRef = this.secrets[0].resource_ref;
    }
  }

  get selected(): any {
    return this.secrets.find((s) => s.resource_ref === this.secretRef);
  }

  secretLabel(secret: any): string {
    const keys = secret?.keys || {};
    const bits = [secret?.name, keys.provider, keys.model].filter(Boolean);
    return bits.join(' · ') || secret?.resource_ref;
  }

  /** A selectable connection must carry its provider and model — the
   *  chat config is derived entirely from it. */
  get selectionComplete(): boolean {
    const keys = this.selected?.keys || {};
    return !!(keys.provider && keys.model);
  }

  get canSave(): boolean {
    return !!this.secretRef && this.selectionComplete;
  }

  save(): void {
    const keys = this.selected?.keys || {};
    const llm: any = {
      provider: keys.provider,
      model: keys.model,
      secret_ref: this.secretRef,
    };
    if (keys.endpoint) llm.endpoint = keys.endpoint;
    this.dialogRef.close(llm);
  }

  close(): void {
    this.dialogRef.close(null);
  }
}
