import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { ExerciseAlignmentState, PacketElement } from '@/api/contracts';
import { curriculumPayload, lessonPayload, renderAt } from '@/test/fixtures';

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.removeItem('regeneration-demo');
});

describe('Lesson lifecycle', () => {
  it('test_regeneration_given_run_fetch_error_with_details_expect_shared_details_disclosure', async () => {
    const rawError = 'BackendDownException: provider request failed';
    window.sessionStorage.setItem('regeneration-demo', 'failed-run');
    const fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: 'The regeneration run could not be loaded.', details: rawError }), {
          status: 503,
          headers: { 'Content-Type': 'application/json' },
        }),
    );
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The regeneration run could not be loaded.');
    const details = alert.querySelector('details');
    expect(details).not.toBeNull();
    expect(details).not.toHaveAttribute('open');
    await userEvent.click(within(alert).getByText('Details'));
    expect(details).toHaveAttribute('open');
    expect(within(alert).getByText(rawError)).toBeInTheDocument();
  });

  it('test_stale_exercise_action_given_state_keys_expect_count_and_request_use_those_keys', async () => {
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
    lesson.state = {
      ...lesson.state,
      findings: [{ ...lesson.state.findings[0], target: { elementId: 'ex1', blockIndex: null } }],
      exerciseStaleness: ['ex2', 'ex1'].map(
        (exerciseKey): ExerciseAlignmentState => ({
          status: 'stale',
          exerciseKey,
          changedSections: [
            {
              sectionId: 'intro',
              beforeText: 'Old text',
              afterText: 'New text',
              summary: 'The section wording changed.',
            },
          ],
          alignment: {
            exerciseKey,
            checkedAgainst: { intro: 'old' },
            sectionSources: { intro: 'Old text' },
            outcome: 'approved',
            reason: null,
            comment: null,
            note: null,
            at: '2026-10-02T00:00:00+00:00',
            decisionId: null,
          },
          history: [],
        }),
      ),
      approvalStages: [
        {
          stage: 'exercises',
          status: 'stale',
          fileSha256: 'current',
          approvedSha256: 'old',
          reviewer: 'human',
          upstreamDiff: '',
        },
      ],
    };
    const run = {
      run_id: 'run-1',
      lesson_id: 'demo',
      key: null,
      mode: 'exercises',
      status: 'waiting',
      steps: [],
      next: [],
      errors: [],
      error_kinds: [],
      proposal_id: null,
      decision: null,
      kept_exercises: [],
    };
    const fetch = vi.fn().mockImplementation((path) => {
      const response =
        path === '/api/lessons/demo/regenerate'
          ? { run_id: 'run-1' }
          : path === '/api/runs/run-1'
            ? run
            : path === '/api/lessons/demo'
              ? lesson
              : curriculumPayload();
      return Promise.resolve(new Response(JSON.stringify(response)));
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });

    await userEvent.click(screen.getByRole('button', { name: 'Regenerate' }));
    await userEvent.click(screen.getByRole('menuitem', { name: /Stale exercises/ }));
    const dialog = screen.getByRole('dialog', { name: 'Regenerate stale exercises' });
    expect(within(dialog).getByText('2 stale exercises will be checked together.')).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Regenerate stale exercises' }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/lessons/demo/regenerate',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ mode: 'exercises', exercise_keys: ['ex2', 'ex1'] }),
        }),
      ),
    );
    expect(await screen.findByRole('status', { name: 'Regeneration progress' })).toHaveTextContent(
      'Regenerate stale exercises',
    );
  });
});
