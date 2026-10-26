import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { curriculumPayload, lessonPayload, renderAt } from '@/test/fixtures';

describe('Lesson toolbar actions', () => {
  it('test_lesson_toolbar_given_render_and_source_views_expect_learner_view_in_overflow', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    expect(screen.getByRole('radio', { name: 'Render' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Source' })).toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: 'Learner' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'More lesson actions' }));
    expect(screen.getByRole('menuitem', { name: 'Learner view' })).toBeInTheDocument();
  });

  it('test_review_menu_given_plan_stage_expect_inline_reason_and_disabled_review_options', async () => {
    renderAt('/lessons/demo?stage=plan', { lesson: lessonPayload() });

    await userEvent.click(screen.getByRole('button', { name: 'Run AI review' }));
    const menu = screen.getByRole('menu');
    expect(within(menu).getByText('Plan has no AI review. Switch to Lesson to review blocks.')).toBeVisible();
    expect(within(menu).getByRole('menuitem', { name: /Whole lesson/ })).toHaveAttribute('aria-disabled', 'true');
    expect(within(menu).getByRole('menuitem', { name: /Selected block/ })).toHaveAttribute('aria-disabled', 'true');
  });
});

describe('Lesson toolbar accepted proposal review', () => {
  afterEach(() => vi.unstubAllGlobals());

  function acceptedLesson(uncovered: string[]) {
    const lesson = lessonPayload();
    lesson.state.proposals = [
      { ...lesson.state.proposals[0], status: 'accepted', allowedDecisions: ['accept', 'reject'] },
    ];
    lesson.state.reviewCoverage = { covered: 3 - uncovered.length, total: 3, uncovered };
    return lesson;
  }

  it('test_proposal_bar_given_all_blocks_accepted_and_uncovered_expect_review_changed_blocks_request', async () => {
    const lesson = acceptedLesson(['intro#0']);
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

    expect(screen.getByText(/changes not reviewed yet/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Accept all' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Review changed blocks' }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/lessons/demo/review/uncovered',
        expect.objectContaining({ method: 'POST' }),
      ),
    );
  });

  it('test_proposal_bar_given_accepted_change_already_reviewed_expect_no_review_changed_blocks', () => {
    renderAt('/lessons/demo', { lesson: acceptedLesson([]) });

    expect(screen.queryByRole('button', { name: 'Review changed blocks' })).toBeNull();
  });
});
