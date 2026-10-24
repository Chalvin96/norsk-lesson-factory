import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { NewLessonSession } from '@/curriculum/new-lesson-session';
import { curriculumPayload, renderAt, workbenchSettingsPayload } from '@/test/fixtures';
import { NewLessonJobsProvider, useNewLessonJobs } from './new-lesson-jobs';

const request = {
  title: 'A very long lesson title that must remain available to assistive technology',
  slug: 'long_lesson',
  learner_outcome: 'Recover.',
  cefr_level: 'A1',
  after_lesson_id: null,
} as const;

function restoreJob() {
  sessionStorage.setItem('workbench-new-lesson-jobs', JSON.stringify([{ jobId: 'restored', request }]));
}

function respond(body: unknown, status = 200) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string) =>
      path.startsWith('/api/jobs/')
        ? new Response(JSON.stringify(body), { status })
        : new Response(JSON.stringify(curriculumPayload())),
    ),
  );
}

function Sessions() {
  const store = useNewLessonJobs();
  return <output aria-label="Session count">{store.sessions.length}</output>;
}

function renderStore() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <NewLessonJobsProvider>
        <Sessions />
      </NewLessonJobsProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.unstubAllGlobals();
});

it('test_completion_given_job_finishes_on_settings_page_expect_persistent_polite_announcement', async () => {
  restoreJob();
  let finish: ((response: Response) => void) | undefined;
  vi.stubGlobal(
    'fetch',
    vi.fn((path: string) =>
      path.startsWith('/api/jobs/')
        ? new Promise<Response>((resolve) => {
            finish = resolve;
          })
        : Promise.resolve(
            new Response(JSON.stringify(path === '/api/settings' ? workbenchSettingsPayload() : curriculumPayload())),
          ),
    ),
  );
  renderAt('/settings');
  const region = screen.getByRole('status', { name: 'Lesson job announcements' });
  expect(region).toHaveAttribute('aria-live', 'polite');
  expect(region).toBeEmptyDOMElement();
  finish?.(new Response(JSON.stringify({ status: 'done' })));
  await waitFor(() => expect(region).toHaveTextContent(`Draft ready: ${request.title}`));
  await userEvent.click(screen.getByRole('link', { name: 'Curriculum' }));
  expect(screen.getByRole('status', { name: 'Lesson job announcements' })).toHaveTextContent(request.title);
});

it('test_recovery_given_server_lost_job_expect_english_error_and_dismissal', async () => {
  restoreJob();
  respond({ detail: 'raw internal 404' }, 404);
  renderAt('/');
  const alert = await screen.findByRole('alert');
  expect(alert).toHaveTextContent(
    'The job is no longer on the server; it may have been lost when the server restarted.',
  );
  expect(alert).not.toHaveTextContent('404');
  await waitFor(() => expect(sessionStorage.getItem('workbench-new-lesson-jobs')).toBe('[]'));
  await userEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }));
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
});

it('test_sessions_given_restored_cancelled_job_expect_removed_session', async () => {
  restoreJob();
  respond({ status: 'cancelled' });
  renderStore();
  await waitFor(() => expect(screen.getByLabelText('Session count')).toHaveTextContent('0'));
});

it('test_indicator_given_long_title_expect_full_accessible_title_and_truncated_text', async () => {
  restoreJob();
  respond({ status: 'running', steps: ['checking draft'] });
  renderAt('/');
  const indicator = await screen.findByTitle(`${request.title}: checking draft`);
  expect(indicator).toHaveAccessibleName(`${request.title}: checking draft`);
  expect(indicator).toHaveAttribute('title', `${request.title}: checking draft`);
  expect(indicator).toHaveClass('truncate');
});

it('test_completion_given_open_success_dialog_and_rerenders_expect_callback_once', async () => {
  const onComplete = vi.fn();
  const state = {
    inputs: request,
    request: null,
    dismiss: vi.fn(),
    close: vi.fn(),
    setRequest: vi.fn(),
    job: {
      isPending: false,
      isSuccess: false,
      isError: false,
      error: new Error(),
      steps: [],
      checkErrors: [],
      findings: [],
      mutate: vi.fn(),
      retry: vi.fn(),
    },
  };
  const view = render(<NewLessonSession lessons={[]} selectedId={undefined} state={state} onComplete={onComplete} />);
  const success = { ...state, request, job: { ...state.job, isSuccess: true } };
  view.rerender(<NewLessonSession lessons={[]} selectedId={undefined} state={success} onComplete={onComplete} />);
  await waitFor(() => expect(onComplete).toHaveBeenCalledOnce());
  view.rerender(
    <NewLessonSession
      lessons={[]}
      selectedId={undefined}
      state={{ ...success, close: vi.fn() }}
      onComplete={onComplete}
    />,
  );
  expect(onComplete).toHaveBeenCalledExactlyOnceWith(request.slug);
});

it.each([
  {
    kind: 'failed',
    status: 200,
    body: { status: 'failed', error: 'Draft validation failed.' },
    message: 'Draft validation failed.',
  },
  {
    kind: 'missing',
    status: 404,
    body: { error: 'missing' },
    message: 'The job is no longer on the server; it may have been lost when the server restarted.',
  },
])(
  'test_recovery_given_restored_$kind_job_expect_error_once_and_new_job_on_retry',
  async ({ status, body, message }) => {
    restoreJob();
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (options?.method === 'POST') return new Response(JSON.stringify({ job_id: 'replacement' }));
      if (path === '/api/jobs/restored') return new Response(JSON.stringify(body), { status });
      if (path === '/api/jobs/replacement') return new Response(JSON.stringify({ status: 'running' }));
      return new Response(JSON.stringify(curriculumPayload()));
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(message);
    await waitFor(() => expect(sessionStorage.getItem('workbench-new-lesson-jobs')).toBe('[]'));
    await userEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/curriculum/lessons',
        expect.objectContaining({ method: 'POST', body: JSON.stringify(request) }),
      ),
    );
    await waitFor(() => expect(sessionStorage.getItem('workbench-new-lesson-jobs')).toContain('replacement'));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  },
);
