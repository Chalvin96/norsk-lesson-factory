import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { LessonPayload } from '@/api/contracts';
import { curriculumPayload, lessonPayload, renderAt, rowElement } from '@/test/fixtures';
import { chatTurnFixture } from '@/test/wire-fixtures';

function json(body: unknown) {
  return new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
}

function chatReply(lesson: LessonPayload) {
  return {
    ...lesson,
    state: {
      ...lesson.state,
      chat: [
        chatTurnFixture({ role: 'author', text: 'Make this natural.' }),
        chatTurnFixture({ role: 'assistant', text: 'I proposed clearer wording.', toolNote: 'proposed 1 block edits' }),
      ],
    },
  };
}

afterEach(() => vi.unstubAllGlobals());

describe('Workbench agents', () => {
  it('test_chat_given_sent_message_expect_pending_turn_then_reply_and_proposal_note', async () => {
    const lesson = lessonPayload();
    let done = false;
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') return json({ job_id: 'chat-job' });
      if (path === '/api/jobs/chat-job') return json(done ? { status: 'done', result: {} } : { status: 'running' });
      return json(chatReply(lesson));
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });
    await userEvent.type(screen.getByRole('textbox', { name: 'Message to chat' }), 'Make this natural.');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText(/Working…/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled();
    expect(fetch).toHaveBeenCalledWith(
      '/api/lessons/demo/chat',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ message: 'Make this natural.', chips: [] }),
        headers: expect.objectContaining({ 'X-Workbench-Request': '1' }),
      }),
    );
    done = true;
    expect(await screen.findByText('I proposed clearer wording.', {}, { timeout: 2500 })).toBeInTheDocument();
    expect(screen.getByText('proposed 1 block edits')).toBeInTheDocument();
    expect(screen.getAllByText('Make this natural.')).toHaveLength(1);
    expect(screen.queryByText(/Working…/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled();
  });

  it('test_chat_given_failed_job_and_retry_expect_inline_error_then_one_completed_author_turn', async () => {
    const lesson = lessonPayload();
    let posts = 0;
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        posts += 1;
        return json({ job_id: `job-${posts}` });
      }
      if (path.startsWith('/api/jobs/'))
        return json(posts === 1 ? { status: 'failed', error: 'Provider unavailable' } : { status: 'done', result: {} });
      return json(chatReply(lesson));
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });
    await userEvent.type(screen.getByRole('textbox', { name: 'Message to chat' }), 'Make this natural.');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Provider unavailable');
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('I proposed clearer wording.')).toBeInTheDocument();
    expect(screen.getAllByText('Make this natural.')).toHaveLength(1);
    expect(posts).toBe(2);
  });

  it('test_workbench_job_given_failed_response_without_error_expect_neutral_fallback', async () => {
    const lesson = lessonPayload();
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') return json({ job_id: 'failed-job' });
      if (path === '/api/jobs/failed-job') return json({ status: 'failed' });
      return json(chatReply(lesson));
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });
    await userEvent.type(screen.getByRole('textbox', { name: 'Message to chat' }), 'Make this natural.');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('The workbench could not finish this job.');
  });

  it('test_review_given_failed_provider_job_expect_friendly_error_and_raw_details_disclosure', async () => {
    const lesson = lessonPayload();
    const friendlyError = 'Z.AI has no credit left. Top up the account, then retry.';
    const rawError = 'BackendDownException: Upstream request failed: Insufficient account funds';
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') return json({ job_id: 'failed-review' });
      if (path === '/api/jobs/failed-review')
        return json({ status: 'failed', error: friendlyError, error_detail: rawError });
      return json(path === '/api/curriculum' ? curriculumPayload() : lesson);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });

    await userEvent.click(screen.getByRole('button', { name: 'Run AI review' }));
    await userEvent.click(screen.getByRole('menuitem', { name: /Whole lesson/ }));

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(friendlyError);
    const details = alert.querySelector('details');
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute('open');
    expect(within(alert).getByText('Details')).toBeInTheDocument();

    await userEvent.click(within(alert).getByText('Details'));

    expect(details).toHaveAttribute('open');
    expect(within(alert).getByText(rawError)).toBeInTheDocument();
  });

  it('test_chat_given_selected_text_and_control_l_expect_exact_row_key_sent_in_chip', async () => {
    const lesson = lessonPayload();
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') return json({ job_id: 'selection-job' });
      if (path.startsWith('/api/jobs/')) return json({ status: 'done', result: {} });
      return json(chatReply(lesson));
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });
    const text = within(rowElement('intro-b0')).getByText('Jeg lager kaffe nå.');
    const range = document.createRange();
    range.selectNodeContents(text);
    act(() => {
      window.getSelection()?.removeAllRanges();
      window.getSelection()?.addRange(range);
    });
    await userEvent.keyboard('{Control>}l{/Control}');
    await userEvent.type(screen.getByRole('textbox', { name: 'Message to chat' }), 'Make this natural.');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    await screen.findByText('I proposed clearer wording.');
    expect(fetch).toHaveBeenCalledWith(
      '/api/lessons/demo/chat',
      expect.objectContaining({
        body: JSON.stringify({
          message: 'Make this natural.',
          chips: [{ kind: 'selection', label: 'Intro', detail: 'Jeg lager kaffe nå.', key: 'intro#0' }],
        }),
      }),
    );
  });

  it.each(['Reject', 'Accept all', 'Reject all'])(
    'test_proposal_given_%s_action_expect_server_decision_and_closed_bar',
    async (label) => {
      const lesson = lessonPayload();
      const updated = { ...lesson, state: { ...lesson.state, proposals: [] } };
      const fetch = vi.fn(async (path: string, options?: RequestInit) =>
        json(
          path === '/api/curriculum' ? curriculumPayload() : options?.method === 'POST' ? { lesson: updated } : updated,
        ),
      );
      vi.stubGlobal('fetch', fetch);
      renderAt('/lessons/demo', { lesson });
      await userEvent.click(screen.getByRole('button', { name: label }));
      await waitFor(() => expect(screen.queryByRole('button', { name: 'Accept all' })).toBeNull());
      const route =
        label === 'Reject'
          ? '/api/lessons/demo/proposals/p1/blocks/intro%230/reject'
          : `/api/lessons/demo/proposals/p1/${label === 'Accept all' ? 'accept' : 'reject'}`;
      expect(fetch).toHaveBeenCalledWith(route, expect.objectContaining({ method: 'POST' }));
    },
  );

  it.each(['conflicted', 'failed'] as const)(
    'test_proposal_given_%s_state_expect_accept_all_disabled_and_reject_available',
    async (state) => {
      const lesson = lessonPayload();
      lesson.state.proposals = lesson.state.proposals.map((proposal) => ({
        ...proposal,
        status: state === 'conflicted' ? 'conflicted' : 'pending',
      }));
      lesson.state.proposalCheck =
        state === 'failed' ? { status: 'failed', errors: ['Invalid syntax'] } : { status: 'passed', errors: [] };
      renderAt('/lessons/demo', { lesson });
      expect(screen.getByRole('button', { name: 'Accept all' })).toBeDisabled();
      expect(screen.getByRole('button', { name: 'Accept' })).toHaveProperty('disabled', state === 'conflicted');
      expect(screen.getByRole('button', { name: 'Reject all' })).toBeEnabled();
      expect(screen.getByRole('button', { name: 'Reject' })).toBeEnabled();
    },
  );

  it('test_review_given_selected_item_then_resolve_and_fix_expect_scoped_job_and_real_actions', async () => {
    const lesson = lessonPayload();
    let current = lesson;
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') {
        if (path.endsWith('/resolve')) {
          current = {
            ...lesson,
            state: {
              ...lesson.state,
              findings: lesson.state.findings.map((finding) =>
                finding.findingId === 'f1' ? { ...finding, status: 'resolved' } : finding,
              ),
            },
          };
          return json({ lesson: current });
        }
        if (path.endsWith('/fix')) current = chatReply(current);
        return json({ job_id: 'review-job' });
      }
      if (path.startsWith('/api/jobs/')) return json({ status: 'done', result: {} });
      return json(path === '/api/curriculum' ? curriculumPayload() : current);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });
    await userEvent.click(within(rowElement('intro-b0')).getByRole('button', { name: 'Edit block' }));
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    await userEvent.click(screen.getByRole('button', { name: 'Run AI review' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Selected block' }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/lessons/demo/review',
        expect.objectContaining({ body: JSON.stringify({ scope: 'item', key: 'intro#0' }) }),
      ),
    );
    await waitFor(() => expect(screen.getByRole('button', { name: 'Run AI review' })).toBeEnabled());
    await userEvent.click(within(rowElement('intro-b1')).getByRole('button', { name: 'Fix' }));
    expect(await screen.findByText('proposed 1 block edits')).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      '/api/lessons/demo/findings/f1/fix',
      expect.objectContaining({ method: 'POST' }),
    );
    await userEvent.click(within(rowElement('intro-b1')).getByRole('button', { name: 'Resolve' }));
    await userEvent.type(
      screen.getByRole('textbox', { name: 'Comment (optional)' }),
      'Accepted the original phrasing.',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Resolve finding' }));
    expect(await screen.findByRole('group', { name: 'Open findings' })).toHaveTextContent(/^1/);
    expect(fetch).toHaveBeenCalledWith(
      '/api/lessons/demo/findings/f1/resolve',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ finding_ids: ['f1'], reason: 'intended', comment: 'Accepted the original phrasing.' }),
      }),
    );
  });

  it('test_review_fix_given_finding_context_expect_readable_author_turn_and_context_chip', async () => {
    const lesson = lessonPayload();
    const authorText = 'Fix finding: bokmal — Improve this sentence.';
    const current: LessonPayload = {
      ...lesson,
      state: {
        ...lesson.state,
        chat: [
          chatTurnFixture({
            role: 'author',
            text: authorText,
            chips: [{ kind: 'finding', label: 'bokmal', detail: 'Jeg liker norsk.', key: 'intro#1' }],
          }),
          chatTurnFixture({
            role: 'assistant',
            text: 'I proposed a wording change.',
            toolNote: 'proposed 1 block edits',
          }),
        ],
      },
    };
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') return json({ job_id: 'fix-job' });
      if (path.startsWith('/api/jobs/')) return json({ status: 'done', result: {} });
      return json(path === '/api/curriculum' ? curriculumPayload() : current);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });

    await userEvent.click(within(rowElement('intro-b1')).getByRole('button', { name: 'Fix' }));

    const transcript = screen.getByRole('list', { name: 'Chat transcript' });
    expect(await within(transcript).findByText(authorText)).toBeInTheDocument();
    expect(within(transcript).getByText('finding')).toBeInTheDocument();
    expect(within(transcript).getByText('bokmal')).toBeInTheDocument();
    expect(within(transcript).getByText('Jeg liker norsk.')).toBeInTheDocument();
    expect(within(transcript).queryByText(/\{"finding_id"/)).toBeNull();
  });

  it('test_curriculum_chat_given_message_expect_reply_only_job_and_persisted_reply', async () => {
    const curriculum = curriculumPayload();
    const updated = {
      ...curriculum,
      chat: [{ role: 'assistant' as const, text: 'These entries cover the basics.', chips: [], toolNote: null }],
    };
    const fetch = vi.fn(async (path: string, options?: RequestInit) =>
      json(
        options?.method === 'POST'
          ? { job_id: 'curriculum-job' }
          : path.startsWith('/api/jobs/')
            ? { status: 'done', result: {} }
            : updated,
      ),
    );
    vi.stubGlobal('fetch', fetch);
    renderAt('/', { curriculum });
    await userEvent.type(screen.getByRole('textbox', { name: 'Message to chat' }), 'Explain the sequence.');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));
    expect(await screen.findByText('These entries cover the basics.')).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      '/api/curriculum/chat',
      expect.objectContaining({ body: JSON.stringify({ message: 'Explain the sequence.' }) }),
    );
  });
});
