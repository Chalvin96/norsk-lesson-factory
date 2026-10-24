import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PacketElement } from '@/api/contracts';
import { curriculumPayload, lessonPayload, renderAt, rowElement } from '@/test/fixtures';
import { proposalFixture } from '@/test/wire-fixtures';

const nextFrame = () => new Promise((resolve) => requestAnimationFrame(resolve));

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.removeItem('regeneration-demo');
});

describe('Lesson review', () => {
  it('test_ask_chat_given_exercise_finding_expect_reason_and_evidence_in_context', async () => {
    const lesson = lessonPayload();
    lesson.state.findings[0] = {
      ...lesson.state.findings[0],
      message: 'Supplied answer retrieval: The source supplies the answer.',
      excerpt: 'Jeg jobber her.',
      suggestedChange: 'Remove the supplied form.',
      quoteEnglish: 'I work here.',
    };
    renderAt('/lessons/demo', { lesson });

    await userEvent.click(within(rowElement('intro-b1')).getByRole('button', { name: 'Ask chat' }));

    const chip = screen.getByRole('button', { name: 'Remove finding Review finding' }).parentElement;
    expect(chip).toHaveTextContent('Supplied answer retrieval: The source supplies the answer.');
    expect(chip).toHaveTextContent('Quoted source: Jeg jobber her.');
    expect(chip).toHaveTextContent('English gloss: I work here.');
    expect(chip).toHaveTextContent('Suggested change: Remove the supplied form.');
  });

  it('test_proposal_row_keep_as_is_given_chat_proposal_with_change_summary_expect_no_keep_action', () => {
    const lesson = lessonPayload();
    lesson.state.proposals = [
      {
        ...lesson.state.proposals[0],
        proposalId: 'chat-proposal',
        origin: 'chat',
        changeSummary: 'Gives the greeting a warmer English gloss.',
      },
    ];
    renderAt('/lessons/demo', { lesson });

    const card = screen.getByRole('region', { name: 'AI proposal' });
    expect(within(card).getByRole('button', { name: 'Accept' })).toBeInTheDocument();
    expect(within(card).queryByRole('button', { name: 'Keep as is' })).toBeNull();
  });

  it('test_proposal_row_keep_as_is_given_regeneration_proposal_expect_keep_action', () => {
    const lesson = lessonPayload();
    lesson.state.proposals = [
      {
        ...lesson.state.proposals[0],
        proposalId: 'regeneration-proposal',
        origin: 'regenerate',
        allowedDecisions: ['accept', 'reject', 'keep'],
        changeSummary: 'Only the explanation now quotes the shorter section line.',
      },
    ];
    renderAt('/lessons/demo', { lesson });

    const card = screen.getByRole('region', { name: 'AI proposal' });
    expect(within(card).getByRole('button', { name: 'Keep as is' })).toBeInTheDocument();
  });

  it('test_exercise_proposals_given_two_pending_blocks_expect_each_accept_and_reject_control_reachable', () => {
    const lesson = lessonPayload();
    const exercise = (id: string): PacketElement => ({
      kind: 'exercise',
      id,
      anchor_id: id,
      operation: 'judge',
      objective_id: 'objective',
      prompt: [{ kind: 'text', value: `Judge ${id}` }],
      payload: { sentence: [{ kind: 'text', value: 'Hei.' }], is_correct: true, feedback: 'Hei is a greeting.' },
    });
    lesson.elements = [exercise('ex1'), exercise('ex2')];
    lesson.state.proposals = ['ex1', 'ex2'].map((key, index) =>
      proposalFixture({
        proposalId: 'fanout-proposal',
        key,
        allowedDecisions: ['accept', 'reject'],
        status: 'pending',
        check: { status: 'passed', errors: [] },
        target: { elementId: key, blockIndex: null },
        before: `Before ${index + 1}`,
        after: `After ${index + 1}`,
        note: 'Refresh this exercise',
      }),
    );
    renderAt('/lessons/demo', { lesson });

    const proposalBlocks = screen.getAllByRole('region', { name: 'AI proposal' });
    expect(proposalBlocks).toHaveLength(2);
    for (const proposalBlock of proposalBlocks) {
      expect(within(proposalBlock).getByRole('button', { name: 'Accept' })).toBeEnabled();
      expect(within(proposalBlock).getByRole('button', { name: 'Reject' })).toBeEnabled();
    }
  });

  it('test_read_mode_given_findings_expect_notes_and_flags_hidden', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    await userEvent.click(screen.getByRole('radio', { name: 'Read' }));

    expect(screen.queryByText('Gloss note')).toBeNull();
    expect(rowElement('intro-b1')).not.toHaveAttribute('data-flag');
  });

  it('test_finding_navigation_given_alt_arrow_down_twice_expect_focus_in_lesson_order', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    await userEvent.keyboard('{Alt>}{ArrowDown}{/Alt}');
    await nextFrame();
    expect(document.activeElement).toHaveAttribute('aria-label', 'AI finding Review finding');

    await userEvent.keyboard('{Alt>}{ArrowDown}{/Alt}');
    await nextFrame();
    expect(document.activeElement).toHaveAttribute('aria-label', 'Check finding Review finding');
  });

  it('test_finding_actions_given_resolve_and_ask_chat_expect_lower_count_and_composer_chip', async () => {
    const lesson = lessonPayload();
    const updated = {
      ...lesson,
      state: {
        ...lesson.state,
        findings: lesson.state.findings.map((finding) =>
          finding.origin === 'ai' ? { ...finding, status: 'resolved' as const } : finding,
        ),
      },
    };
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementation((path, options) =>
          Promise.resolve(
            new Response(
              JSON.stringify(
                path === '/api/curriculum'
                  ? curriculumPayload()
                  : options?.method === 'POST'
                    ? { lesson: updated }
                    : updated,
              ),
            ),
          ),
        ),
    );
    renderAt('/lessons/demo', { lesson });
    const note = within(rowElement('intro-b1'));

    await userEvent.click(note.getByRole('button', { name: 'Ask chat' }));
    await userEvent.click(note.getByRole('button', { name: 'Resolve' }));
    await userEvent.click(screen.getByRole('button', { name: 'Resolve finding' }));

    expect(await screen.findByRole('group', { name: 'Open findings' })).toHaveTextContent(/^1/);
    expect(note.getByText('Resolved')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Remove finding Review finding' })).toBeInTheDocument();
  });

  it('test_similar_findings_given_keyboard_reason_expect_same_reason_and_comment_for_each_id', async () => {
    const lesson = lessonPayload();
    const similar = {
      ...lesson.state.findings[0],
      findingId: 'f3',
      target: { elementId: 'intro', blockIndex: 0 },
      explanation: 'This excerpt also appears in the other block.',
    };
    lesson.state.findings.push(similar);
    const updated = {
      ...lesson,
      state: {
        ...lesson.state,
        findings: lesson.state.findings.map((finding) =>
          finding.origin === 'ai' ? { ...finding, status: 'resolved' as const } : finding,
        ),
      },
    };
    const fetch = vi
      .fn()
      .mockImplementation((path, options) =>
        Promise.resolve(
          new Response(
            JSON.stringify(
              path === '/api/curriculum'
                ? curriculumPayload()
                : options?.method === 'POST'
                  ? { lesson: updated }
                  : lesson,
            ),
          ),
        ),
      );
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });

    await userEvent.click(within(rowElement('intro-b1')).getByRole('button', { name: 'Resolve all similar (1)' }));

    expect(screen.queryByRole('list', { name: 'Findings to resolve' })).not.toBeInTheDocument();
    const dialog = within(screen.getByRole('dialog'));
    expect(
      dialog.getByText(lesson.state.findings[0].message || lesson.state.findings[0].explanation),
    ).toBeInTheDocument();
    expect(dialog.getByText(similar.message || similar.explanation)).toBeInTheDocument();
    expect(dialog.getByRole('textbox', { name: 'Comment (optional)' })).toHaveAttribute('maxLength', '500');
    expect(dialog.getByText('0 / 500')).toBeInTheDocument();
    const intended = screen.getByRole('radio', { name: 'Intended' });
    expect(intended).toBeChecked();
    intended.focus();
    await userEvent.keyboard('{ArrowRight}');
    expect(screen.getByRole('radio', { name: 'Not applicable' })).toBeChecked();
    await userEvent.type(screen.getByRole('textbox', { name: 'Comment (optional)' }), 'Handled together');
    expect(dialog.getByText('16 / 500')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Resolve 2 findings' }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/lessons/demo/findings/f1/resolve',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ finding_ids: ['f1', 'f3'], reason: 'not_applicable', comment: 'Handled together' }),
        }),
      ),
    );
  });

  it('test_check_finding_given_matching_ai_finding_expect_no_resolve_similar_action', () => {
    const lesson = lessonPayload();
    lesson.state.findings.push({
      ...lesson.state.findings[1],
      findingId: 'ai-match',
      target: { elementId: 'intro', blockIndex: 0 },
      origin: 'ai',
    });
    renderAt('/lessons/demo', { lesson });

    const checkNote = screen.getByRole('article', { name: 'Check finding Review finding' });
    expect(within(checkNote).queryByText('1 similar elsewhere')).not.toBeInTheDocument();
    expect(within(checkNote).queryByRole('button', { name: 'Resolve all similar (1)' })).not.toBeInTheDocument();
  });

  it('test_proposal_bar_given_only_proposal_accepted_expect_server_state_and_bar_closed', async () => {
    const lesson = lessonPayload();
    const updated = { ...lesson, state: { ...lesson.state, proposals: [] } };
    const fetch = vi
      .fn()
      .mockImplementation((path, options) =>
        Promise.resolve(
          new Response(
            JSON.stringify(
              path === '/api/curriculum'
                ? curriculumPayload()
                : options?.method === 'POST'
                  ? { lesson: updated }
                  : updated,
            ),
          ),
        ),
      );
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });
    await userEvent.click(screen.getByRole('button', { name: 'Accept' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Accept all' })).toBeNull());
    expect(fetch).toHaveBeenCalledWith(
      '/api/lessons/demo/proposals/p1/blocks/intro%230/accept',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it.each(['intro#0', 'lesson.md', 'exercises.yaml'])(
    'test_accepted_proposal_given_multiple_uncovered_blocks_expect_total_review_scope_and_uncovered_request',
    async (key) => {
      const lesson = lessonPayload();
      lesson.state.proposals = [
        {
          ...lesson.state.proposals[0],
          key,
          status: 'accepted' as const,
          allowedDecisions: ['accept', 'reject'],
        },
      ];
      lesson.state.reviewCoverage = { covered: 0, total: 3, uncovered: ['intro#0', 'intro#1', 'exercise:e1'] };
      const fetch = vi.fn(async (path: string, options?: RequestInit) =>
        Promise.resolve(
          new Response(
            JSON.stringify(
              path === '/api/curriculum'
                ? curriculumPayload()
                : options?.method === 'POST'
                  ? { job_id: 'review-job' }
                  : lesson,
            ),
          ),
        ),
      );
      vi.stubGlobal('fetch', fetch);
      renderAt('/lessons/demo', { lesson });

      const scope = key === 'intro#0' ? screen : within(screen.getByRole('region', { name: `${key} proposal` }));
      const card = scope.getByRole('region', { name: 'AI proposal' });
      const button = within(card).getByRole('button', { name: 'Review 3 uncovered blocks' });
      expect(button).toHaveAttribute('title', 'Run AI review on all uncovered blocks in this lesson');
      await userEvent.click(button);

      await waitFor(() =>
        expect(fetch).toHaveBeenCalledWith(
          '/api/lessons/demo/review/uncovered',
          expect.objectContaining({ method: 'POST' }),
        ),
      );
    },
  );

  it.each(
    ['intro#0', 'lesson.md', 'exercises.yaml'].flatMap((key) =>
      ['@lesson-metadata', '@exercise-placement-order'].map((target) => ({ key, target })),
    ),
  )(
    'test_accepted_proposal_given_synthetic_uncovered_target_expect_whole_lesson_block_count_and_tooltip',
    async ({ key, target }) => {
      const lesson = lessonPayload();
      lesson.state.proposals = [
        { ...lesson.state.proposals[0], key, status: 'accepted', allowedDecisions: ['accept', 'reject'] },
      ];
      lesson.state.reviewCoverage = { covered: 3, total: 5, uncovered: ['intro#0', target] };
      const fetch = vi.fn(
        async (path: string, options?: RequestInit) =>
          new Response(
            JSON.stringify(
              path === '/api/curriculum'
                ? curriculumPayload()
                : options?.method === 'POST'
                  ? { job_id: 'review-job' }
                  : lesson,
            ),
          ),
      );
      vi.stubGlobal('fetch', fetch);
      renderAt('/lessons/demo', { lesson });

      const scope = key === 'intro#0' ? screen : within(screen.getByRole('region', { name: `${key} proposal` }));
      const card = scope.getByRole('region', { name: 'AI proposal' });
      const button = within(card).getByRole('button', { name: `Review whole lesson (${lesson.blocks.length} blocks)` });
      expect(button).toHaveAttribute(
        'title',
        'Run AI review on every block in this lesson because lesson structure is uncovered',
      );
      await userEvent.click(button);

      await waitFor(() =>
        expect(fetch).toHaveBeenCalledWith(
          '/api/lessons/demo/review/uncovered',
          expect.objectContaining({ method: 'POST' }),
        ),
      );
    },
  );

  it('test_accepted_proposal_given_changed_block_already_reviewed_expect_no_review_action', () => {
    const lesson = lessonPayload();
    lesson.state.proposals = [
      {
        ...lesson.state.proposals[0],
        status: 'accepted' as const,
        allowedDecisions: ['accept', 'reject'],
      },
    ];
    lesson.state.reviewCoverage = { covered: 1, total: 1, uncovered: [] };
    renderAt('/lessons/demo', { lesson });

    const card = screen.getByRole('region', { name: 'AI proposal' });
    expect(within(card).queryByRole('button', { name: /Review .* uncovered blocks/ })).toBeNull();
  });
});
