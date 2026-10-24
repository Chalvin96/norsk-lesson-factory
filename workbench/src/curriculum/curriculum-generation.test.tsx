import { act, cleanup, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CurriculumLesson } from '@/api/contracts';
import { curriculumPayload, lessonPayload, renderAt } from '@/test/fixtures';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe('Curriculum generation', () => {
  it('test_create_lesson_given_missing_fields_expect_specific_disabled_reason', async () => {
    renderAt('/');
    await userEvent.click(screen.getByRole('button', { name: 'New lesson' }));
    const create = await screen.findByRole('button', { name: 'Create lesson' });
    expect(create).toBeEnabled();
    expect(create).toHaveAttribute('aria-disabled', 'true');
    expect(create).toHaveAccessibleDescription('Fill in the title to continue.');
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    create.focus();
    expect(create).toHaveFocus();
    await userEvent.keyboard('{Enter}');
    expect(screen.getByLabelText('Title')).toHaveFocus();
    expect(fetch).not.toHaveBeenCalled();
    expect(screen.getByText('Fill in the title to continue.')).not.toHaveAttribute('aria-live');
    await userEvent.type(screen.getByLabelText('Title'), 'Present tense');
    expect(create).toHaveAccessibleDescription('Fill in the learner outcome to continue.');
    await userEvent.click(create);
    expect(screen.getByLabelText('Learner outcome')).toHaveFocus();
    expect(fetch).not.toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText('Learner outcome'), 'Talk about habits.');
    expect(create).toBeEnabled();
    expect(screen.queryByText(/to continue\./)).not.toBeInTheDocument();
  });

  it('test_create_lesson_given_invalid_or_duplicate_slug_expect_disabled_reason', async () => {
    renderAt('/');
    await userEvent.click(screen.getByRole('button', { name: 'New lesson' }));
    await userEvent.type(await screen.findByLabelText('Title'), 'Present tense');
    await userEvent.type(screen.getByLabelText('Learner outcome'), 'Talk about habits.');
    await userEvent.clear(screen.getByLabelText('Slug'));
    await userEvent.type(screen.getByLabelText('Slug'), 'INVALID');
    const create = screen.getByRole('button', { name: 'Create lesson' });
    expect(create).toBeEnabled();
    expect(create).toHaveAttribute('aria-disabled', 'true');
    expect(create).toHaveAccessibleDescription('Use a valid slug to continue.');
    expect(screen.getByText('Use a valid slug to continue.')).toHaveClass('text-destructive');
    await userEvent.click(create);
    expect(screen.getByLabelText('Slug')).toHaveFocus();
    expect(screen.getByLabelText('Slug')).toHaveAccessibleDescription('Use a valid slug to continue.');
    expect(screen.getAllByText('Use a valid slug to continue.')).toHaveLength(1);
    await userEvent.clear(screen.getByLabelText('Slug'));
    await userEvent.type(screen.getByLabelText('Slug'), 'demo');
    expect(create).toHaveAccessibleDescription('Choose an unused slug to continue.');
    expect(screen.getByLabelText('Slug')).toHaveAttribute('aria-invalid', 'true');
  });

  it('test_new_lesson_dialog_given_cancel_then_navigation_expect_dialog_closed', async () => {
    renderAt('/');
    await userEvent.click(screen.getByRole('button', { name: 'New lesson' }));
    await userEvent.click(await within(await screen.findByRole('dialog')).findByRole('button', { name: 'Cancel' }));
    await userEvent.click(screen.getByRole('link', { name: 'Settings' }));
    await userEvent.click(screen.getByRole('link', { name: 'Curriculum' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('test_edit_inputs_given_failed_job_and_navigation_expect_submitted_values_restored', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, options?: RequestInit) => {
        if (options?.method === 'POST') return json({ job_id: 'edit-inputs' });
        return json(path.startsWith('/api/jobs/') ? { status: 'failed', error: 'Failed.' } : curriculumPayload());
      }),
    );
    renderAt('/');
    await userEvent.click(screen.getByRole('button', { name: 'New lesson' }));
    await userEvent.type(await screen.findByLabelText('Title'), 'Keep inputs');
    await userEvent.type(screen.getByLabelText('Learner outcome'), 'Recover me.');
    await userEvent.click(screen.getByRole('button', { name: 'Create lesson' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Edit inputs' }));
    act(() => {
      window.history.pushState(null, '', '/settings');
      window.dispatchEvent(new PopStateEvent('popstate'));
    });
    await screen.findByRole('heading', { name: 'Settings' });
    await userEvent.click(screen.getByRole('link', { name: 'Curriculum' }));
    expect(await screen.findByLabelText('Title')).toHaveValue('Keep inputs');
    expect(screen.getByLabelText('Slug')).toHaveValue('keep_inputs');
    expect(screen.getByLabelText('Learner outcome')).toHaveValue('Recover me.');
    expect(screen.getByRole('combobox', { name: 'Position' })).toHaveTextContent('After Demo lesson');
  });

  it('test_job_tracking_given_unavailable_session_storage_expect_running_indicator', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('Storage unavailable');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('Storage unavailable');
    });
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, options?: RequestInit) => {
        if (options?.method === 'POST') return json({ job_id: 'storage-unavailable' });
        if (path.startsWith('/api/jobs/')) return json({ status: 'running', steps: ['drafting lesson'] });
        return json(curriculumPayload());
      }),
    );
    renderAt('/');
    await userEvent.click(screen.getByRole('button', { name: 'New lesson' }));
    await userEvent.type(await screen.findByLabelText('Title'), 'Safe job');
    await userEvent.type(screen.getByLabelText('Learner outcome'), 'Recover.');
    await userEvent.click(screen.getByRole('button', { name: 'Create lesson' }));
    await screen.findByText('drafting lesson');
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }));
    expect(within(screen.getByRole('toolbar', { name: 'Curriculum actions' })).getByRole('status')).toHaveTextContent(
      'Safe job',
    );
  });

  it.each(['running', 'failed'])(
    'test_job_indicator_given_%s_job_and_navigation_away_and_back_expect_retained_indicator',
    async (status) => {
      vi.stubGlobal(
        'fetch',
        vi.fn(async (path: string, options?: RequestInit) => {
          if (options?.method === 'POST') return json({ job_id: 'navigation-job' });
          if (path.startsWith('/api/jobs/'))
            return json({ status, steps: ['drafting lesson'], error: 'Drafting failed.' });
          return json(curriculumPayload());
        }),
      );
      renderAt('/');
      await userEvent.click(screen.getByRole('button', { name: 'New lesson' }));
      await userEvent.type(await screen.findByLabelText('Title'), 'Keep tracking');
      await userEvent.type(screen.getByLabelText('Learner outcome'), 'Keep my job.');
      await userEvent.click(screen.getByRole('button', { name: 'Create lesson' }));
      await screen.findByText('drafting lesson');
      await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }));
      await userEvent.click(screen.getByRole('link', { name: 'Settings' }));
      await userEvent.click(screen.getByRole('link', { name: 'Curriculum' }));
      const toolbar = within(await screen.findByRole('toolbar', { name: 'Curriculum actions' }));
      expect(await toolbar.findByRole(status === 'failed' ? 'alert' : 'status')).toHaveTextContent('Keep tracking');
      if (status === 'running') {
        expect(JSON.parse(sessionStorage.getItem('workbench-new-lesson-jobs') ?? '[]')).toEqual([
          {
            jobId: 'navigation-job',
            request: {
              title: 'Keep tracking',
              slug: 'keep_tracking',
              learner_outcome: 'Keep my job.',
              cefr_level: 'A1',
              after_lesson_id: 'demo',
            },
          },
        ]);
      } else {
        await userEvent.click(toolbar.getByRole('button', { name: 'Edit inputs' }));
        expect(await screen.findByLabelText('Title')).toHaveValue('Keep tracking');
        expect(screen.getByLabelText('Learner outcome')).toHaveValue('Keep my job.');
        await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
      }
    },
    15000,
  );

  it('test_job_recovery_given_stored_running_job_expect_restored_indicator_and_polling', async () => {
    sessionStorage.setItem(
      'workbench-new-lesson-jobs',
      JSON.stringify([
        {
          jobId: 'restored',
          request: {
            title: 'Restored draft',
            slug: 'restored_draft',
            learner_outcome: 'Recover.',
            cefr_level: 'A1',
            after_lesson_id: null,
          },
        },
      ]),
    );
    const fetch = vi.fn(async (path: string) =>
      json(path.startsWith('/api/jobs/') ? { status: 'running', steps: ['checking draft'] } : curriculumPayload()),
    );
    vi.stubGlobal('fetch', fetch);
    renderAt('/');
    const toolbar = within(screen.getByRole('toolbar', { name: 'Curriculum actions' }));
    expect(await toolbar.findByRole('status')).toHaveTextContent('Restored draft: checking draft');
    expect(fetch).toHaveBeenCalledWith('/api/jobs/restored', expect.anything());
  });

  it.each(['done', 'failed', 'cancelled'])(
    'test_job_recovery_given_stored_%s_job_expect_outstanding_id_cleared',
    async (status) => {
      sessionStorage.setItem(
        'workbench-new-lesson-jobs',
        JSON.stringify([
          {
            jobId: 'finished',
            request: {
              title: 'Finished draft',
              slug: 'finished_draft',
              learner_outcome: 'Recover.',
              cefr_level: 'A1',
              after_lesson_id: null,
            },
          },
        ]),
      );
      vi.stubGlobal(
        'fetch',
        vi.fn(async (path: string) =>
          path.startsWith('/api/jobs/') ? json({ status, error: 'Failed.' }) : json(curriculumPayload()),
        ),
      );
      renderAt('/');
      await waitFor(() => expect(sessionStorage.getItem('workbench-new-lesson-jobs')).toBe('[]'));
      if (status !== 'failed')
        expect(
          within(screen.getByRole('toolbar', { name: 'Curriculum actions' })).queryByRole('status'),
        ).not.toBeInTheDocument();
    },
  );

  it('test_source_status_given_not_drafted_row_and_duplicate_status_chip_expect_source_once', () => {
    const data = curriculumPayload();
    data.lessons[0].source = 'missing';
    data.entries.demo.statusChips = [
      { label: 'not drafted', tone: 'neutral' },
      { label: 'Needs review', tone: 'warning' },
    ];
    renderAt('/', { curriculum: data });
    const row = screen.getByRole('button', { name: 'Open Demo lesson details' }).closest('li');
    if (!row) throw new Error('Missing curriculum row');
    expect(within(row).getAllByText(/not drafted/i)).toHaveLength(1);
    expect(within(row).getByText('Needs review')).toBeInTheDocument();
  });

  it('test_new_lesson_dialog_given_title_slug_edit_and_duplicates_expect_inline_validation', async () => {
    const user = userEvent.setup();
    renderAt('/');
    await user.click(screen.getByRole('button', { name: 'New lesson' }));
    await screen.findByLabelText('Title');
    await user.type(screen.getByLabelText('Title'), 'Useful Norwegian');
    await waitFor(() => expect(screen.getByLabelText('Slug')).toHaveValue('useful_norwegian'));
    await user.clear(screen.getByLabelText('Slug'));
    await user.type(screen.getByLabelText('Slug'), 'demo');
    await user.type(screen.getByLabelText('Learner outcome'), 'Ask a question.');
    expect(screen.getByText('This slug already exists.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Create lesson' })).toHaveAttribute('aria-disabled', 'true');
    await user.clear(screen.getByLabelText('Slug'));
    await user.type(screen.getByLabelText('Slug'), 'custom_slug');
    await user.type(screen.getByLabelText('Title'), ' title');
    expect(screen.getByLabelText('Slug')).toHaveValue('custom_slug');
    expect(screen.getByRole('button', { name: 'Create lesson' })).toBeEnabled();
    expect(screen.getByRole('combobox', { name: 'Position' })).toHaveTextContent('After Demo lesson');
  });

  it('test_slug_given_norwegian_title_expect_ascii_suggestion_until_manual_edit', async () => {
    renderAt('/');
    await userEvent.click(screen.getByRole('button', { name: 'New lesson' }));
    await userEvent.type(await screen.findByLabelText('Title'), 'Ærlig Øvelse på Å si hei!');
    expect(screen.getByLabelText('Slug')).toHaveValue('aerlig_ovelse_paa_aa_si_hei');
    await userEvent.clear(screen.getByLabelText('Slug'));
    await userEvent.type(screen.getByLabelText('Title'), ' igjen');
    expect(screen.getByLabelText('Slug')).toHaveValue('');
  });

  it('test_source_chips_given_source_in_status_chips_expect_one_source_per_row', () => {
    const data = curriculumPayload();
    data.entries.demo.statusChips.push({ label: 'Canonical', tone: 'neutral' });
    data.entries.second.statusChips.push({ label: 'Draft', tone: 'draft' });
    renderAt('/', { curriculum: data });
    expect(
      within(screen.getByRole('button', { name: 'Open Demo lesson details' })).getAllByText('Canonical'),
    ).toHaveLength(1);
    expect(
      within(screen.getByRole('button', { name: 'Open Second lesson details' })).getAllByText('Draft'),
    ).toHaveLength(1);
  });

  it('test_switcher_given_curriculum_known_and_lesson_loading_expect_position_immediately', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => {})),
    );
    renderAt('/lessons/second');
    expect(await screen.findByText('Loading lesson…')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /02.*Second lesson.*Switch lesson/ })).toBeInTheDocument();
  });

  it('test_progress_given_later_poll_steps_expect_current_drafting_and_repair_stages', async () => {
    let steps = ['adding to curriculum'];
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, options?: RequestInit) => {
        if (options?.method === 'POST') return json({ job_id: 'stages' });
        if (path.startsWith('/api/jobs/')) return json({ status: 'running', steps });
        return json(curriculumPayload());
      }),
    );
    renderAt('/');
    await userEvent.click(screen.getByRole('button', { name: 'New lesson' }));
    await userEvent.type(await screen.findByLabelText('Title'), 'Stage test');
    await userEvent.type(screen.getByLabelText('Learner outcome'), 'Test stages.');
    await userEvent.click(screen.getByRole('button', { name: 'Create lesson' }));
    expect(await screen.findByText('adding to curriculum')).toBeInTheDocument();
    steps = [...steps, 'drafting lesson'];
    expect(await screen.findByText('drafting lesson', {}, { timeout: 2500 })).toBeInTheDocument();
    steps = [...steps, 'repairing (1/2)'];
    expect(await screen.findByText('repairing (1/2)', {}, { timeout: 2500 })).toBeInTheDocument();
  });

  it('test_closed_job_given_later_failure_expect_visible_error_retry_and_preserved_edit_inputs', async () => {
    let failed = false;
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') return json({ job_id: 'closed' });
      if (path.startsWith('/api/jobs/'))
        return json(
          failed
            ? { status: 'failed', error: 'The author service is unavailable.', steps: ['drafting lesson'] }
            : { status: 'running', steps: ['drafting lesson'] },
        );
      return json(curriculumPayload());
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/');
    await userEvent.click(screen.getByRole('button', { name: 'New lesson' }));
    await userEvent.type(await screen.findByLabelText('Title'), 'Recover me');
    await userEvent.type(screen.getByLabelText('Learner outcome'), 'Keep these inputs.');
    await userEvent.click(screen.getByRole('button', { name: 'Create lesson' }));
    await screen.findByText('drafting lesson');
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }));
    const toolbar = within(screen.getByRole('toolbar', { name: 'Curriculum actions' }));
    expect(toolbar.getByRole('status')).toHaveTextContent('Recover me');
    expect(toolbar.getByRole('status')).toHaveTextContent('drafting lesson');
    failed = true;
    expect(await toolbar.findByRole('alert', {}, { timeout: 2500 })).toHaveTextContent(
      'The author service is unavailable.',
    );
    await userEvent.click(toolbar.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(2));
    await userEvent.click(await toolbar.findByRole('button', { name: 'Edit inputs' }));
    expect(await screen.findByLabelText('Title')).toHaveValue('Recover me');
    expect(screen.getByLabelText('Learner outcome')).toHaveValue('Keep these inputs.');
  });

  it('test_new_lesson_given_another_closed_job_expect_separate_form_and_retained_job', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, options?: RequestInit) => {
        if (options?.method === 'POST') return json({ job_id: 'first' });
        if (path.startsWith('/api/jobs/')) return json({ status: 'running', steps: ['drafting lesson'] });
        return json(curriculumPayload());
      }),
    );
    renderAt('/');
    await userEvent.click(screen.getByRole('button', { name: 'New lesson' }));
    await userEvent.type(await screen.findByLabelText('Title'), 'First draft');
    await userEvent.type(screen.getByLabelText('Learner outcome'), 'Keep drafting.');
    await userEvent.click(screen.getByRole('button', { name: 'Create lesson' }));
    await screen.findByText('drafting lesson');
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }));
    await userEvent.click(screen.getByRole('button', { name: 'New lesson' }));
    expect(await screen.findByLabelText('Title')).toHaveValue('');
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Cancel' }));
    expect(within(screen.getByRole('toolbar', { name: 'Curriculum actions' })).getByRole('status')).toHaveTextContent(
      'First draft',
    );
  });

  it('test_new_lesson_progress_given_running_then_done_expect_steps_and_open_draft_navigation', async () => {
    const user = userEvent.setup();
    const data = curriculumPayload();
    let done = false;
    const draft = lessonPayload({ lessonId: 'new_lesson', source: 'draft' });
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') return json({ job_id: 'new-job' });
      if (path.startsWith('/api/jobs/'))
        return json(
          done
            ? { status: 'done', steps: ['adding to curriculum', 'drafting lesson', 'checking', 'done'], result: {} }
            : { status: 'running', steps: ['adding to curriculum', 'drafting lesson'] },
        );
      return json(path === '/api/curriculum' ? data : draft);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/');
    await user.click(screen.getByRole('button', { name: 'New lesson' }));
    await screen.findByLabelText('Title');
    await user.type(screen.getByLabelText('Title'), 'New lesson');
    await waitFor(() => {
      expect(screen.getByLabelText('Title')).toHaveValue('New lesson');
      expect(screen.getByLabelText('Slug')).toHaveValue('new_lesson');
    });
    await user.type(screen.getByLabelText('Learner outcome'), 'Ask a question.');
    await user.click(screen.getByRole('button', { name: 'Create lesson' }));
    expect(await screen.findByText('drafting lesson')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Open lesson' })).toBeNull();
    done = true;
    const open = await within(screen.getByRole('dialog')).findByRole(
      'button',
      { name: 'Open lesson' },
      { timeout: 2500 },
    );
    await user.click(open);
    expect(window.location.pathname).toBe('/lessons/new_lesson');
    expect(fetch).toHaveBeenCalledWith(
      '/api/curriculum/lessons',
      expect.objectContaining({
        body: JSON.stringify({
          title: 'New lesson',
          learner_outcome: 'Ask a question.',
          cefr_level: 'A1',
          slug: 'new_lesson',
          after_lesson_id: 'demo',
        }),
      }),
    );
  });

  it('test_new_lesson_job_given_completed_draft_expect_lesson_switcher_refreshed_from_curriculum', async () => {
    const data = curriculumPayload();
    const created: CurriculumLesson = {
      lessonId: 'new_lesson',
      title: 'New lesson',
      source: 'draft',
      cefrLevel: 'A1',
      sequenceIndex: 3,
    };
    const updated = {
      ...data,
      lessons: [...data.lessons, created],
      entries: {
        ...data.entries,
        new_lesson: {
          statusChips: [],
          dependsOn: [],
          helpfulPrerequisites: [],
          cefrLevel: 'A1',
          learnerOutcome: 'Ask a question.',
          findings: [],
        },
      },
    };
    let done = false;
    let createdOnServer = false;
    const draft = lessonPayload({
      lessonId: created.lessonId,
      lesson: { title: created.title, cefrLevel: 'A1', kind: 'grammar', goal: 'Ask a question.' },
    });
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') return json({ job_id: 'new-job' });
      if (path.startsWith('/api/jobs/')) {
        if (done) createdOnServer = true;
        return json(done ? { status: 'done', result: { lesson_id: created.lessonId } } : { status: 'running' });
      }
      if (path === '/api/curriculum') return json(createdOnServer ? updated : data);
      return json(draft);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/', { curriculum: data });
    await userEvent.click(screen.getByRole('button', { name: 'New lesson' }));
    await screen.findByLabelText('Title');
    await userEvent.type(screen.getByLabelText('Title'), created.title);
    await userEvent.type(screen.getByLabelText('Learner outcome'), 'Ask a question.');
    await userEvent.click(screen.getByRole('button', { name: 'Create lesson' }));
    expect(await screen.findByText('Waiting to start…')).toBeInTheDocument();
    done = true;
    await userEvent.click(
      await within(screen.getByRole('dialog')).findByRole('button', { name: 'Open lesson' }, { timeout: 2500 }),
    );

    expect(await screen.findByRole('button', { name: /04.*New lesson/ })).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith('/api/curriculum', expect.objectContaining({ cache: 'no-store' }));
  });

  it('test_new_lesson_job_given_dialog_closed_while_running_expect_curriculum_refreshed_on_completion', async () => {
    const data = curriculumPayload();
    const created: CurriculumLesson = {
      lessonId: 'weather_talk',
      title: 'Weather talk',
      source: 'draft',
      cefrLevel: 'A1',
      sequenceIndex: 3,
    };
    const updated = {
      ...data,
      lessons: [...data.lessons, created],
      entries: {
        ...data.entries,
        weather_talk: {
          statusChips: [],
          dependsOn: [],
          helpfulPrerequisites: [],
          cefrLevel: 'A1',
          learnerOutcome: 'Talk about weather.',
          findings: [],
        },
      },
    };
    let done = false;
    let createdOnServer = false;
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') return json({ job_id: 'new-job' });
      if (path.startsWith('/api/jobs/')) {
        if (done) createdOnServer = true;
        return json(done ? { status: 'done', result: { lesson_id: created.lessonId } } : { status: 'running' });
      }
      return json(createdOnServer ? updated : data);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/', { curriculum: data });
    await userEvent.click(screen.getByRole('button', { name: 'New lesson' }));
    await screen.findByLabelText('Title');
    await userEvent.type(screen.getByLabelText('Title'), created.title);
    await userEvent.type(screen.getByLabelText('Learner outcome'), 'Talk about weather.');
    await userEvent.click(screen.getByRole('button', { name: 'Create lesson' }));
    expect(await screen.findByText('Waiting to start…')).toBeInTheDocument();

    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    const toolbar = within(screen.getByRole('toolbar', { name: 'Curriculum actions' }));
    expect(toolbar.getByRole('status')).toHaveTextContent(created.title);
    done = true;

    const rowButton = await screen.findByRole('button', { name: `Open ${created.title} details` }, { timeout: 4000 });
    expect(rowButton).toHaveAttribute('aria-pressed', 'true');
  });

  it('test_new_lesson_progress_given_failed_repair_expect_failure_and_last_check_errors', async () => {
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') return json({ job_id: 'failed-job' });
      if (path.startsWith('/api/jobs/'))
        return json({
          status: 'failed',
          error: 'Lesson generation failed: plan.objectives: At least one objective is required.',
          error_detail: 'ValidationError: raw schema diagnostics\nMissing required objective',
          steps: ['drafting lesson', 'checking', 'repairing', 'checking'],
          check: { errors: ['Missing required objective'] },
        });
      return json(curriculumPayload());
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/');
    await userEvent.click(screen.getByRole('button', { name: 'New lesson' }));
    await screen.findByLabelText('Title');
    await userEvent.type(screen.getByLabelText('Title'), 'New lesson');
    await userEvent.type(screen.getByLabelText('Learner outcome'), 'Ask a question.');
    await userEvent.click(screen.getByRole('button', { name: 'Create lesson' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('At least one objective is required.');
    const details = screen.getByText('Details');
    expect(details.closest('details')).not.toHaveAttribute('open');
    expect(screen.getByText(/raw schema diagnostics/).closest('details')).toBe(details.closest('details'));
    details.focus();
    expect(details).toHaveFocus();
    await userEvent.click(details);
    expect(details.closest('details')).toHaveAttribute('open');
    expect(screen.getByText(/raw schema diagnostics/)).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Edit inputs' }));
    expect(screen.getByLabelText('Title')).toHaveValue('New lesson');
    expect(screen.getByLabelText('Slug')).toHaveValue('new_lesson');
    expect(screen.getByLabelText('Learner outcome')).toHaveValue('Ask a question.');
    await userEvent.click(screen.getByRole('button', { name: 'Create lesson' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('At least one objective is required.');
    expect(fetch.mock.calls.filter(([, options]) => options?.method === 'POST')).toHaveLength(2);
  });
});
