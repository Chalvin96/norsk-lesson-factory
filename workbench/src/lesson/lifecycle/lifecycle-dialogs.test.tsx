import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { StageApproval } from '@/api/contracts';
import { curriculumPayload, lessonPayload, renderAt } from '@/test/fixtures';
import { proposalFixture } from '@/test/wire-fixtures';

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

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  sessionStorage.clear();
});

describe('Owner lifecycle', () => {
  it('test_approve_dialog_given_stage_blockers_expect_listed_blockers_and_disabled_confirmation', async () => {
    const lesson = lessonPayload();
    lesson.draft.exists = true;
    lesson.state.approvalStages = [
      stage(),
      stage({ stage: 'exercises', status: 'stale', upstreamDiff: '- old lesson\n+ new lesson' }),
    ];
    lesson.state.approveBlockers = [
      { kind: 'open_proposal', stage: 'lesson', target: 'proposal' },
      {
        kind: 'review_coverage',
        stage: 'exercises',
        count: 2,
        uncovered: ['exercise-a', 'exercise-b'],
        target: 'review',
      },
    ];
    lesson.state.approvable = false;
    renderAt('/lessons/demo', { lesson });
    await userEvent.click(screen.getByRole('button', { name: 'Lesson: Open proposal' }));
    const dialog = await screen.findByRole('dialog', { name: 'Approve stages' });
    expect(within(dialog).getByRole('region', { name: 'lesson approval' })).toHaveTextContent('Lesson: Open proposal');
    expect(within(dialog).getByRole('region', { name: 'exercises approval' })).toHaveTextContent(
      'Exercises: 2 blocks not reviewed',
    );
    expect(within(dialog).getByText(/old lesson/)).toHaveTextContent('+ new lesson');
    expect(within(dialog).getByRole('button', { name: 'Approve' })).toBeDisabled();
    expect(within(dialog).getByText('Owner acceptance is not qualified Norwegian review.')).toBeInTheDocument();
  });

  it('test_approve_dialog_given_ready_stages_expect_commit_toast_and_updated_chips', async () => {
    const lesson = lessonPayload();
    lesson.draft.exists = true;
    lesson.state.approvalStages = [stage()];
    lesson.state.approveBlockers = [];
    lesson.state.approvable = true;
    const accepted = {
      ...lesson,
      state: {
        ...lesson.state,
        approvalStages: [stage({ status: 'approved' })],
        statusChips: [{ label: 'owner accepted', tone: 'ok' }],
      },
    };
    const fetch = vi.fn(async (path: string, options?: RequestInit) =>
      json(
        path === '/api/curriculum'
          ? curriculumPayload()
          : options?.method === 'POST'
            ? { commit: 'abc123456', lesson: accepted }
            : accepted,
      ),
    );
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });
    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Owner acceptance is not qualified Norwegian review.')).toBeInTheDocument();
    expect(within(dialog).getByText('Ready')).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Approve' }));
    expect(await screen.findByText('Owner accepted abc1234')).toBeInTheDocument();
    expect(await screen.findByText('owner accepted')).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith('/api/lessons/demo/approve', expect.objectContaining({ method: 'POST' }));
  });

  it('test_approve_dialog_given_ready_plan_and_blocked_content_stages_expect_plan_only_request', async () => {
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
    const accepted = {
      ...lesson,
      state: {
        ...lesson.state,
        approvalStages: [
          stage({ stage: 'plan', status: 'approved' }),
          stage({ stage: 'lesson', status: 'stale' }),
          stage({ stage: 'exercises', status: 'stale' }),
        ],
        approveBlockers: [
          { kind: 'review_coverage', stage: 'exercises', count: 17, uncovered: ['exercise-a'], target: 'review' },
        ],
      },
    };
    const fetch = vi.fn(async (path: string, options?: RequestInit) =>
      json(
        path === '/api/curriculum'
          ? curriculumPayload()
          : options?.method === 'POST'
            ? { commit: 'abc123456', stages: ['plan'], lesson: accepted }
            : accepted,
      ),
    );
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });

    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    const dialog = await screen.findByRole('dialog', { name: 'Approve stages' });
    const planApproval = within(dialog).getByRole('region', { name: 'plan approval' });
    expect(within(planApproval).getByRole('checkbox', { name: 'Approve plan' })).toBeChecked();
    expect(within(dialog).getByRole('region', { name: 'lesson approval' })).toHaveTextContent(
      'Lesson: 151 blocks not reviewed',
    );
    expect(within(dialog).getByRole('region', { name: 'exercises approval' })).toHaveTextContent(
      'Exercises: 17 blocks not reviewed',
    );
    await userEvent.click(within(dialog).getByRole('button', { name: 'Approve' }));

    expect(await screen.findAllByText('Owner accepted abc1234')).not.toHaveLength(0);
    expect(fetch).toHaveBeenCalledWith(
      '/api/lessons/demo/approve',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ stages: ['plan'] }) }),
    );
  });

  it('test_approve_dialog_given_unchecked_ready_stage_expect_request_omits_that_stage', async () => {
    const lesson = lessonPayload();
    lesson.draft.exists = true;
    lesson.state.approvalStages = [stage({ stage: 'plan' }), stage()];
    lesson.state.approveBlockers = [];
    lesson.state.approvable = true;
    const accepted = lessonPayload();
    accepted.state.approvalStages = [stage({ stage: 'plan', status: 'approved' })];
    const fetch = vi.fn(async (path: string, options?: RequestInit) =>
      json(
        path === '/api/curriculum'
          ? curriculumPayload()
          : options?.method === 'POST'
            ? { commit: 'abc123456', stages: ['plan'], lesson: accepted }
            : accepted,
      ),
    );
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });

    await userEvent.click(screen.getByRole('button', { name: 'Approve' }));
    const dialog = await screen.findByRole('dialog', { name: 'Approve stages' });
    await userEvent.click(within(dialog).getByRole('checkbox', { name: 'Approve lesson' }));
    await userEvent.click(within(dialog).getByRole('button', { name: 'Approve' }));

    expect(fetch).toHaveBeenCalledWith(
      '/api/lessons/demo/approve',
      expect.objectContaining({ method: 'POST', body: JSON.stringify({ stages: ['plan'] }) }),
    );
  });

  it('test_approve_dialog_given_global_check_error_kind_expect_findings_route_and_disabled_confirmation', async () => {
    const lesson = lessonPayload();
    lesson.draft.exists = true;
    lesson.state.approvalStages = [stage()];
    lesson.state.approvable = false;
    lesson.state.approveBlockers = [{ kind: 'check_errors', stage: null, target: 'findings' }];
    renderAt('/lessons/demo', { lesson });

    await userEvent.click(screen.getByRole('button', { name: 'Blocking check errors' }));
    const dialog = await screen.findByRole('dialog', { name: 'Approve stages' });
    expect(within(dialog).getByRole('list', { name: 'General approval blockers' })).toHaveTextContent(
      'Blocking check errors',
    );
    expect(within(dialog).getByRole('button', { name: 'Go to check findings' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Approve' })).toBeDisabled();
  });

  it('test_promote_dialog_given_canonical_conflict_expect_diff_pull_and_confirmed_discard_choices', async () => {
    const lesson = lessonPayload();
    lesson.draft.exists = true;
    lesson.state.promotable = true;
    const fetch = vi.fn(async (path: string, options?: RequestInit) =>
      path.endsWith('/promote') && options?.method === 'POST'
        ? json({ error: 'canonical_changed', diff: '- draft text\n+ canonical text' }, 409)
        : json({ lesson }),
    );
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });
    await userEvent.click(screen.getByRole('button', { name: 'More lesson actions' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Promote…' }));
    const confirmation = await screen.findByRole('dialog', { name: 'Promote draft' });
    await userEvent.click(within(confirmation).getByRole('button', { name: 'Promote' }));
    const conflict = await screen.findByRole('dialog', { name: 'Canonical lesson changed' });
    expect(within(conflict).getByText(/draft text/)).toHaveTextContent('canonical text');
    expect(within(conflict).getByRole('button', { name: 'Pull changes into draft' })).toBeEnabled();
    await userEvent.click(within(conflict).getByRole('button', { name: 'Discard draft' }));
    const discard = await screen.findByRole('dialog', { name: 'Discard draft?' });
    expect(within(discard).getByText(/committed history/)).toBeInTheDocument();
    await userEvent.click(within(discard).getByRole('button', { name: 'Cancel' }));
    expect(fetch.mock.calls.filter(([path]) => path.endsWith('/discard'))).toHaveLength(0);
  });

  it('test_canonical_pull_given_canonical_already_matches_expect_provenance_success_message', async () => {
    const lesson = lessonPayload();
    lesson.draft.exists = true;
    lesson.state.promotable = true;
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (path.endsWith('/promote') && options?.method === 'POST') {
        return json({ error: 'canonical_changed', diff: 'Canonical already matches the draft.' }, 409);
      }
      if (path.endsWith('/pull')) return json({ lesson, message: 'Canonical already matches; provenance updated.' });
      if (path === '/api/curriculum') return json(curriculumPayload());
      return json(lesson);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });

    await userEvent.click(screen.getByRole('button', { name: 'More lesson actions' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Promote…' }));
    await userEvent.click(
      within(await screen.findByRole('dialog', { name: 'Promote draft' })).getByRole('button', { name: 'Promote' }),
    );
    const conflict = await screen.findByRole('dialog', { name: 'Canonical lesson changed' });
    await userEvent.click(within(conflict).getByRole('button', { name: 'Pull changes into draft' }));

    expect(await screen.findByText('Canonical already matches; provenance updated.')).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('test_promote_dialog_given_unapproved_stage_expect_disabled_confirmation', async () => {
    const lesson = lessonPayload();
    lesson.state.promotable = false;
    renderAt('/lessons/demo', { lesson });
    await userEvent.click(screen.getByRole('button', { name: 'More lesson actions' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Promote…' }));
    expect(
      within(await screen.findByRole('dialog', { name: 'Promote draft' })).getByRole('button', { name: 'Promote' }),
    ).toBeDisabled();
  });

  it('test_promote_dialog_given_projected_canonical_blocker_expect_review_action_opens_diff', async () => {
    const lesson = lessonPayload();
    lesson.draft.exists = true;
    lesson.state.promotable = false;
    lesson.state.promotionBlockers = [{ kind: 'canonical_changed', stage: 'lesson', target: 'canonical' }];
    const fetch = vi.fn(async (path: string, options?: RequestInit) =>
      path.endsWith('/promote') && options?.method === 'POST'
        ? json({ error: 'canonical_changed', diff: '- draft text\n+ canonical text' }, 409)
        : json({ lesson }),
    );
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });

    await userEvent.click(screen.getByRole('button', { name: 'More lesson actions' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Promote…' }));
    const promotion = await screen.findByRole('dialog', { name: 'Promote draft' });
    expect(within(promotion).getByRole('button', { name: 'Promote' })).toBeDisabled();
    await userEvent.click(within(promotion).getByRole('button', { name: 'Review canonical changes' }));

    const conflict = await screen.findByRole('dialog', { name: 'Canonical lesson changed' });
    expect(within(conflict).getByText(/draft text/)).toHaveTextContent('canonical text');
    expect(fetch).toHaveBeenCalledWith('/api/lessons/demo/promote', expect.objectContaining({ method: 'POST' }));
  });

  it('test_regenerate_dialog_given_current_section_expect_scope_ticket_node_progress_and_proposal', async () => {
    const lesson = lessonPayload();
    lesson.state.proposals = [];
    const regenerated = lessonPayload();
    regenerated.state.proposals = [
      proposalFixture({
        proposalId: 'regenerated1',
        key: 'intro#0',
        status: 'pending',
        check: { status: 'passed', errors: [] },
        target: { elementId: 'intro', blockIndex: 0 },
        before: 'Original greeting',
        after: 'New regenerated greeting',
        note: 'Section rewritten',
        allowedDecisions: ['accept', 'reject'],
      }),
    ];
    let waiting = false;
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') return json({ run_id: 'run1', job_id: 'job1' });
      if (path === '/api/runs/run1') {
        waiting = true;
        return json({
          run_id: 'run1',
          lesson_id: 'demo',
          key: 'intro',
          mode: 'scope',
          status: 'waiting',
          steps: ['generate', 'check', 'review', 'waiting for you'],
          next: ['interrupt'],
          errors: [],
          error_kinds: [],
          proposal_id: 'p1',
          decision: null,
          kept_exercises: [],
        });
      }
      return json(path === '/api/curriculum' ? curriculumPayload() : waiting ? regenerated : lesson);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      top: 0,
      bottom: 100,
      left: 0,
      right: 100,
      width: 100,
      height: 100,
      x: 0,
      y: 0,
      toJSON() {},
    });
    await userEvent.click(screen.getByRole('button', { name: 'Regenerate' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Current section…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Regenerate scope' });
    expect(within(dialog).getByText('intro')).toBeInTheDocument();
    await userEvent.click(within(dialog).getByRole('button', { name: 'Regenerate' }));
    expect(await screen.findByRole('status', { name: 'Regeneration progress' })).toHaveTextContent('waiting for you');
    expect(fetch).toHaveBeenCalledWith(
      '/api/lessons/demo/regenerate',
      expect.objectContaining({ body: JSON.stringify({ mode: 'scope', key: 'intro' }) }),
    );
    const proposal = await screen.findByRole('region', { name: 'AI proposal' });
    expect(proposal).toHaveTextContent('New regenerated greeting');
    expect(proposal).toHaveTextContent('Section rewritten');
  });

  it('test_regenerate_run_given_cancelled_status_expect_final_cancelled_without_failed_error', async () => {
    const lesson = lessonPayload();
    lesson.state.proposals = [];
    sessionStorage.setItem('regeneration-demo', 'run1');
    const fetch = vi.fn(async (path: string) => {
      if (path === '/api/runs/run1')
        return json({
          run_id: 'run1',
          lesson_id: 'demo',
          key: 'intro',
          mode: 'scope',
          status: 'cancelled',
          steps: ['generate'],
          next: [],
          errors: ['Regeneration was cancelled.'],
          error_kinds: ['cancelled'],
          proposal_id: null,
          decision: null,
        });
      return json(path === '/api/curriculum' ? curriculumPayload() : lesson);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });

    const panel = await screen.findByRole('status', { name: 'Regeneration progress' });
    expect(panel).toHaveTextContent('Regeneration cancelled');
    expect(panel).not.toHaveTextContent('Regeneration was cancelled.');
    expect(panel).not.toHaveTextContent('Completed');
  });

  it('test_regenerate_run_given_shutdown_interrupted_status_expect_resume_notice_without_failed_error', async () => {
    const lesson = lessonPayload();
    lesson.state.proposals = [];
    sessionStorage.setItem('regeneration-demo', 'run1');
    const fetch = vi.fn(async (path: string) => {
      if (path === '/api/runs/run1')
        return json({
          run_id: 'run1',
          lesson_id: 'demo',
          key: 'intro',
          mode: 'scope',
          status: 'shutdown_interrupted',
          steps: ['generate'],
          next: ['generate'],
          errors: ['Workbench server is shutting down.'],
          error_kinds: ['shutdown_interrupted'],
          proposal_id: null,
          decision: null,
        });
      return json(path === '/api/curriculum' ? curriculumPayload() : lesson);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });

    const panel = await screen.findByRole('status', { name: 'Regeneration progress' });
    expect(panel).toHaveTextContent('Interrupted by server shutdown · will resume on next start');
    expect(panel).not.toHaveTextContent('Workbench server is shutting down.');
    expect(panel).not.toHaveTextContent('Completed');
  });

  it('test_regeneration_given_running_run_and_cancel_expect_cancelled_terminal_state_without_proposal', async () => {
    const lesson = lessonPayload();
    lesson.state.proposals = [];
    let cancelled = false;
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (path === '/api/lessons/demo/regenerate' && options?.method === 'POST') {
        return json({ run_id: 'run-cancel', job_id: 'job-cancel' });
      }
      if (path === '/api/runs/run-cancel') {
        return json({
          run_id: 'run-cancel',
          lesson_id: 'demo',
          key: 'intro',
          mode: 'scope',
          status: cancelled ? 'cancelled' : 'running',
          steps: ['generate'],
          next: ['check'],
          errors: cancelled ? ['Regeneration was cancelled.'] : [],
          error_kinds: cancelled ? ['cancelled'] : [],
          proposal_id: null,
          decision: null,
          kept_exercises: [],
          job_id: 'job-cancel',
        });
      }
      if (path === '/api/jobs/job-cancel/cancel' && options?.method === 'POST') {
        cancelled = true;
        return json({ cancelled: true });
      }
      return json(path === '/api/curriculum' ? curriculumPayload() : lesson);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      top: 0,
      bottom: 100,
      left: 0,
      right: 100,
      width: 100,
      height: 100,
      x: 0,
      y: 0,
      toJSON() {},
    });

    await userEvent.click(screen.getByRole('button', { name: 'Regenerate' }));
    await userEvent.click(screen.getByRole('menuitem', { name: 'Current section…' }));
    const dialog = await screen.findByRole('dialog', { name: 'Regenerate scope' });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Regenerate' }));

    const progress = await screen.findByRole('status', { name: 'Regeneration progress' });
    expect(progress).toHaveTextContent('generate');
    await userEvent.click(within(progress).getByRole('button', { name: 'Cancel' }));

    expect(fetch).toHaveBeenCalledWith('/api/jobs/job-cancel/cancel', expect.objectContaining({ method: 'POST' }));
    expect(await screen.findByText('Regeneration cancelled')).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'AI proposal' })).not.toBeInTheDocument();
  });

  it('test_reloaded_regeneration_given_active_run_job_id_expect_cancel_action_restored', async () => {
    const lesson = lessonPayload();
    lesson.state.proposals = [];
    sessionStorage.setItem('regeneration-demo', 'run-restored');
    let cancelled = false;
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (path === '/api/runs/run-restored') {
        return json({
          run_id: 'run-restored',
          lesson_id: 'demo',
          key: 'intro',
          mode: 'scope',
          status: cancelled ? 'cancelled' : 'running',
          steps: ['generate'],
          next: ['check'],
          errors: [],
          error_kinds: [],
          proposal_id: null,
          decision: null,
          kept_exercises: [],
          job_id: 'job-restored',
        });
      }
      if (path === '/api/jobs/job-restored/cancel' && options?.method === 'POST') {
        cancelled = true;
        return json({ cancelled: true });
      }
      return json(path === '/api/curriculum' ? curriculumPayload() : lesson);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });

    const progress = await screen.findByRole('status', { name: 'Regeneration progress' });
    await userEvent.click(within(progress).getByRole('button', { name: 'Cancel' }));

    expect(fetch).toHaveBeenCalledWith('/api/jobs/job-restored/cancel', expect.objectContaining({ method: 'POST' }));
    expect(await screen.findByText('Regeneration cancelled')).toBeInTheDocument();
  });
});
