import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { StageApproval } from '@/api/contracts';
import { curriculumPayload, lessonPayload, renderAt } from '@/test/fixtures';

function stage(overrides: Partial<StageApproval> = {}): StageApproval {
  return {
    stage: 'lesson',
    status: 'needs_approval',
    fileSha256: 'current',
    approvedSha256: 'old',
    reviewer: 'human',
    upstreamDiff: '',
    ...overrides,
  };
}

describe('Lesson toolbar approval status', () => {
  it('test_lesson_toolbar_given_render_and_source_views_expect_learner_view_in_overflow', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    expect(screen.getByRole('radio', { name: 'Render' })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'Source' })).toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: 'Learner' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'More lesson actions' }));
    expect(screen.getByRole('menuitem', { name: 'Learner view' })).toBeInTheDocument();
  });

  it('test_approval_toolbar_given_ready_plan_and_blocked_content_stages_expect_approve_opens_dialog', async () => {
    const lesson = lessonPayload();
    lesson.draft.exists = true;
    lesson.state.approvalStages = [
      stage({ stage: 'plan' }),
      stage({ stage: 'lesson', status: 'stale' }),
      stage({ stage: 'exercises', status: 'stale' }),
    ];
    lesson.state.approveBlockers = [
      { kind: 'review_coverage', stage: 'lesson', count: 151, uncovered: ['reading-a'], target: 'review' },
      { kind: 'review_coverage', stage: 'exercises', count: 17, uncovered: ['exercise-a'], target: 'review' },
    ];
    lesson.state.approvable = false;
    renderAt('/lessons/demo', { lesson });

    const approve = screen.getByRole('button', { name: 'Approve' });
    expect(approve).toHaveAttribute('aria-disabled', 'false');
    await userEvent.click(approve);
    const dialog = await screen.findByRole('dialog', { name: 'Approve stages' });
    expect(within(dialog).getByRole('checkbox', { name: 'Approve plan' })).toBeEnabled();
    expect(within(dialog).getByText('Lesson: 151 blocks not reviewed')).toBeInTheDocument();
    expect(within(dialog).getByText('Exercises: 17 blocks not reviewed')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Approve' })).toBeEnabled();
  });

  it('test_approval_toolbar_given_only_blocked_stages_expect_dialog_lists_blockers_inline', async () => {
    const lesson = lessonPayload();
    lesson.draft.exists = true;
    lesson.state.approvalStages = [stage(), stage({ stage: 'exercises', status: 'stale' })];
    lesson.state.approveBlockers = [
      { kind: 'open_proposal', stage: 'lesson', target: 'proposal' },
      { kind: 'check_errors', stage: 'exercises', target: 'findings' },
    ];
    lesson.state.approvable = false;
    renderAt('/lessons/demo', { lesson });

    const approve = screen.getByRole('button', { name: 'Approve' });
    expect(approve).toHaveAttribute('aria-disabled', 'false');
    expect(screen.getByText('Blocked:')).toBeInTheDocument();
    expect(screen.queryByText('Ready to approve')).not.toBeInTheDocument();
    await userEvent.click(approve);
    const dialog = await screen.findByRole('dialog', { name: 'Approve stages' });
    expect(within(dialog).getByText('Lesson: Open proposal')).toBeInTheDocument();
    expect(within(dialog).getByText('Exercises: Blocking check errors')).toBeInTheDocument();
    expect(within(dialog).queryByRole('checkbox')).not.toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Approve' })).toBeDisabled();
  });

  it('test_approval_toolbar_given_no_gated_stages_expect_disabled_approval_with_blocker_title', () => {
    const lesson = lessonPayload();
    lesson.draft.exists = true;
    lesson.state.statusChips = [
      { label: 'Canonical', tone: 'neutral' },
      { label: ' canonical ', tone: 'neutral' },
    ];
    lesson.state.approvalStages = [stage({ status: 'approved' })];
    lesson.state.approveBlockers = [{ kind: 'open_proposal', stage: 'lesson', target: 'proposal' }];
    renderAt('/lessons/demo', { lesson });

    const approve = screen.getByRole('button', { name: 'Approve' });
    expect(approve).toHaveAttribute('aria-disabled', 'true');
    expect(approve).toHaveClass('opacity-55', 'disabled:opacity-50');
    expect(approve).toHaveAttribute('title', 'Approval blocked: Lesson: Open proposal');
    expect(screen.getAllByText('Canonical')).toHaveLength(1);
  });

  it('test_review_menu_given_plan_stage_expect_inline_reason_and_disabled_review_options', async () => {
    renderAt('/lessons/demo?stage=plan', { lesson: lessonPayload() });

    await userEvent.click(screen.getByRole('button', { name: 'Run AI review' }));
    const menu = screen.getByRole('menu');
    expect(within(menu).getByText('Plan has no AI review. Switch to Lesson to review blocks.')).toBeVisible();
    expect(within(menu).getByRole('menuitem', { name: /Whole lesson/ })).toHaveAttribute('aria-disabled', 'true');
    expect(within(menu).getByRole('menuitem', { name: /Selected block/ })).toHaveAttribute('aria-disabled', 'true');
  });

  it('test_approval_toolbar_given_no_blockers_expect_ready_state', () => {
    const lesson = lessonPayload();
    lesson.draft.exists = true;
    lesson.state.approvalStages = [stage()];
    lesson.state.approveBlockers = [];
    lesson.state.approvable = true;
    renderAt('/lessons/demo', { lesson });

    expect(screen.getByText('Ready to approve')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve' })).toHaveAttribute('aria-disabled', 'false');
  });

  it('test_approval_toolbar_given_freshly_accepted_stages_expect_disabled_approval_with_status', () => {
    const lesson = lessonPayload();
    lesson.draft.exists = true;
    lesson.state.approveBlockers = [];
    lesson.state.approvable = false;
    renderAt('/lessons/demo', { lesson });

    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Approve' })).toHaveAttribute(
      'title',
      'All stages already have owner acceptance and are fresh',
    );
    expect(screen.getByText('All stages already have owner acceptance and are fresh')).toBeInTheDocument();
  });

  it('test_approval_toolbar_given_canonical_lesson_without_draft_expect_approval_unavailable', () => {
    const lesson = lessonPayload();
    lesson.state.approveBlockers = [];
    lesson.state.reviewCoverage = { covered: 0, total: 168, uncovered: ['a-first-meeting'] };
    renderAt('/lessons/demo', { lesson });

    expect(screen.queryByText('Ready to approve')).not.toBeInTheDocument();
    expect(screen.getByText('Draft required')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled();
  });

  it('test_approval_toolbar_given_stage_coverage_blockers_expect_stage_labeled_review_counts', () => {
    const lesson = lessonPayload();
    lesson.draft.exists = true;
    lesson.state.approveBlockers = [
      { kind: 'review_coverage', stage: 'lesson', count: 151, uncovered: ['reading-a'], target: 'review' },
      { kind: 'review_coverage', stage: 'exercises', count: 17, uncovered: ['exercise-a'], target: 'review' },
    ];
    renderAt('/lessons/demo', { lesson });

    expect(screen.getByRole('button', { name: 'Lesson: 151 blocks not reviewed' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Exercises: 17 blocks not reviewed' })).toBeInTheDocument();
    expect(screen.queryByText(/review coverage incomplete/)).not.toBeInTheDocument();
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
