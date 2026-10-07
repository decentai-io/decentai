import { ChatDetailComponent } from './chat-detail.component';

describe('ChatDetailComponent approval lifecycle', () => {
  function deferred<T>() {
    let resolve!: (value: T) => void;
    const promise = new Promise<T>((done) => (resolve = done));
    return { promise, resolve };
  }

  function componentWith(resolveApproval: jasmine.Spy, session: Record<string, any> = {}) {
    let component!: ChatDetailComponent;
    const state = {
      replaceApprovals: jasmine.createSpy('replaceApprovals').and.callFake(
        (approvals: any[]) => { component.pendingApprovals = approvals; },
      ),
      addError: jasmine.createSpy('addError'),
      acceptSequence: () => true,
    };
    component = new ChatDetailComponent(
      { resolveApproval, ...session } as any,
      {} as any,
      {} as any,
      {} as any,
      {} as any,
      {} as any,
      {} as any,
      state as any,
      // The viewport: how wide the page is, which nothing here asks.
      {} as any,
      // The voice: nothing here is said aloud.
      { arrived: () => {}, stop: () => {}, refresh: async () => false } as any,
    );
    return component;
  }

  it('submits once, preserves the outcome, and ignores a delayed replay', async () => {
    const result = deferred<{ data: { status: string } }>();
    const resolveApproval = jasmine
      .createSpy('resolveApproval')
      .and.returnValue(result.promise);
    const component = componentWith(resolveApproval);
    const request = {
      approval_id: 'appr_1',
      function: 'notebook.sync.push',
      permission_level: 3,
      timeout_seconds: 120,
      inputs: { notebook: 'personal' },
    };
    (component as any).addApproval(request);

    const first = component.onApprovalDecision({
      approval_id: 'appr_1',
      decision: 'approve',
    });
    const duplicate = component.onApprovalDecision({
      approval_id: 'appr_1',
      decision: 'approve',
    });

    expect(resolveApproval).toHaveBeenCalledTimes(1);
    expect(component.pendingApprovals[0].status).toBe('approving');

    result.resolve({ data: { status: 'approved' } });
    await Promise.all([first, duplicate]);
    expect(component.pendingApprovals[0].status).toBe('approved');

    (component as any).addApproval(request);
    expect(component.pendingApprovals.length).toBe(1);
    expect(component.pendingApprovals[0].status).toBe('approved');
  });

  it('restores the card when submission fails so the user can retry', async () => {
    const resolveApproval = jasmine
      .createSpy('resolveApproval')
      .and.resolveTo({ error: 'Network unavailable' });
    const component = componentWith(resolveApproval);
    (component as any).addApproval({
      approval_id: 'appr_2',
      function: 'notebook.sync.push',
      permission_level: 3,
      timeout_seconds: 120,
    });

    await component.onApprovalDecision({
      approval_id: 'appr_2',
      decision: 'approve',
    });

    expect(component.pendingApprovals[0].status).toBe('pending');
    expect((component as any).state.addError).toHaveBeenCalledWith(
      'approval', 'approval_failed', 'Network unavailable', true,
    );
  });

  it('answers a file question with the ref the upload came back with', async () => {
    /** AI:Chat:UploadFile answers {resource: {...}}: the ref is inside
     *  it, and that ref is the answer the agent reads. */
    const uploadChatFile = jasmine.createSpy('uploadChatFile').and.resolveTo({
      data: { resource: { resource_ref: 'fil_9', values: { filename: 'cv.pdf' } } },
    });
    const answerQuestion = jasmine.createSpy('answerQuestion').and.resolveTo({
      data: { status: 'answered' },
    });
    const component = componentWith(
      jasmine.createSpy('resolveApproval'), { uploadChatFile, answerQuestion });
    component.chat_id = 'chat_1';
    (component as any).addApproval({
      approval_id: 'appr_3', kind: 'question', expects: 'file',
      function: 'applications.intake.read', question: 'Which document?',
    });

    await component.onApprovalDecision({
      approval_id: 'appr_3', decision: 'answer',
      file: new File(['x'], 'cv.pdf', { type: 'application/pdf' }),
    });

    expect(uploadChatFile).toHaveBeenCalledTimes(1);
    expect(answerQuestion).toHaveBeenCalledWith('appr_3', 'fil_9');
    expect(component.pendingApprovals[0].status).toBe('answered');
    expect((component as any).state.addError).not.toHaveBeenCalled();
  });

  it('puts the card back to waiting when the answer could not even be sent', async () => {
    const uploadChatFile = jasmine.createSpy('uploadChatFile').and.rejectWith(
      new Error('Failed to read file'));
    const component = componentWith(
      jasmine.createSpy('resolveApproval'), { uploadChatFile });
    component.chat_id = 'chat_1';
    (component as any).addApproval({
      approval_id: 'appr_4', kind: 'question', expects: 'file',
      function: 'applications.intake.read', question: 'Which document?',
    });

    await component.onApprovalDecision({
      approval_id: 'appr_4', decision: 'answer',
      file: new File(['x'], 'cv.pdf'),
    });

    expect(component.pendingApprovals[0].status).toBe('pending');
    expect((component as any).state.addError).toHaveBeenCalledWith(
      'approval', 'approval_failed', jasmine.any(String), true,
    );
  });

  it('lets go of a card that was already decided somewhere else', async () => {
    const resolveApproval = jasmine.createSpy('resolveApproval').and.resolveTo({
      error: 'Approval is already approved.', code: 'not_pending',
    });
    const component = componentWith(resolveApproval);
    (component as any).addApproval({
      approval_id: 'appr_5', function: 'notebook.sync.push', permission_level: 3,
    });

    await component.onApprovalDecision({ approval_id: 'appr_5', decision: 'approve' });

    expect(component.pendingApprovals[0].status).toBe('expired');
    expect(component.waitingApprovals.length).toBe(0);
    expect((component as any).state.addError).toHaveBeenCalledWith(
      'approval', 'approval_closed', 'Approval is already approved.', false,
    );
  });
});

describe('ChatDetailComponent title', () => {
  function titled(title: string): string {
    return JSON.stringify({
      endpoint: 'AI:Chat:Event', data: { event: 'chat_titled', title },
    });
  }

  function component() {
    return new ChatDetailComponent(
      {} as any, {} as any, {} as any, {} as any, {} as any, {} as any, {} as any,
      { acceptSequence: () => true } as any,
      {} as any,
      { arrived: () => {}, stop: () => {}, refresh: async () => false } as any,
    );
  }

  it('takes the runtime\'s name after a rename that changed nothing', async () => {
    /** Opening the title and leaving it as it was is not a rename: the
     *  names the runtime gives the chat afterwards must still land. */
    const page = component();
    page.title = 'New chat';
    page.startTitleEdit();
    await page.saveTitle();

    (page as any).handleSocketMessage(titled('Quarterly figures'));
    expect(page.title).toBe('Quarterly figures');
  });

  it('leaves the title alone while the person is typing one', () => {
    const page = component();
    page.title = 'New chat';
    page.startTitleEdit();

    (page as any).handleSocketMessage(titled('Quarterly figures'));
    expect(page.title).toBe('New chat');
  });
});
