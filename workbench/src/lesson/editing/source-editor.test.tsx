import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { curriculumPayload, lessonPayload, renderAt } from '@/test/fixtures';

const K_EDITED_SOURCE = '# Edited before navigation';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

async function editSource(): Promise<void> {
  await userEvent.click(screen.getByRole('radio', { name: 'Source' }));
  const source = screen.getByRole('textbox', { name: 'Lesson source' });
  await userEvent.clear(source);
  await userEvent.type(source, K_EDITED_SOURCE);
}

afterEach(() => vi.unstubAllGlobals());

describe('Source editor navigation', () => {
  it('test_source_editor_given_learner_view_switch_during_write_expect_busy_state_cleared_after_success', async () => {
    const lessonId = 'source-write-race';
    const lesson = lessonPayload({
      lessonId,
      draft: { exists: true, startedFrom: 'synthetic', dirty: true, lastCommit: null },
    });
    const completedWrites: (() => void)[] = [];
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PUT')
        return new Promise<Response>((resolve) => {
          completedWrites.push(() =>
            resolve(json({ lesson: lessonPayload({ ...lesson, lessonSource: K_EDITED_SOURCE }) })),
          );
        });
      if (String(input) === '/api/curriculum') return json(curriculumPayload());
      return json(lesson);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt(`/lessons/${lessonId}`, { lesson });

    await editSource();
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        `/api/lessons/${lessonId}/files/lesson.md`,
        expect.objectContaining({ method: 'PUT', body: JSON.stringify({ text: K_EDITED_SOURCE }) }),
      ),
    );
    try {
      await userEvent.click(screen.getByRole('button', { name: 'More lesson actions' }));
      await userEvent.click(screen.getByRole('menuitem', { name: 'Learner view' }));
      expect(await screen.findByRole('region', { name: 'Learner view' })).toBeInTheDocument();
      const completeWrite = completedWrites[0];
      if (!completeWrite) throw new Error('Source write did not start.');
      completeWrite();
      await waitFor(() => expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled());
    } finally {
      completedWrites[0]?.();
    }
  });

  it('test_source_editor_given_curriculum_navigation_before_debounce_expect_pending_write_sent', async () => {
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PUT') return json({ lesson: lessonPayload({ lessonSource: K_EDITED_SOURCE }) });
      if (String(input) === '/api/curriculum') return json(curriculumPayload());
      return json(lessonPayload({ lessonSource: K_EDITED_SOURCE }));
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    await editSource();
    await userEvent.click(screen.getByRole('link', { name: 'Curriculum' }));

    expect(window.location.pathname).toBe('/');
    expect(screen.queryByRole('dialog', { name: 'Leave with unsaved edits?' })).not.toBeInTheDocument();
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/lessons/demo/files/lesson.md',
        expect.objectContaining({ method: 'PUT', body: JSON.stringify({ text: K_EDITED_SOURCE }) }),
      ),
    );
  });

  it('test_source_editor_given_lesson_switch_before_debounce_expect_pending_write_sent', async () => {
    const secondLesson = lessonPayload({
      lessonId: 'second',
      lessonSource: '# Second source',
      lesson: { title: 'Second lesson', cefrLevel: 'A1', kind: 'grammar', goal: 'Learn the second lesson.' },
    });
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/lessons/second') return json(secondLesson);
      if (init?.method === 'PUT') return json({ lesson: lessonPayload({ lessonSource: K_EDITED_SOURCE }) });
      return json(curriculumPayload());
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    await editSource();
    await userEvent.click(screen.getByRole('button', { name: /Switch lesson/ }));
    await userEvent.click(await screen.findByRole('link', { name: /Second lesson/ }));

    expect(window.location.pathname).toBe('/lessons/second');
    expect(screen.queryByRole('dialog', { name: 'Leave with unsaved edits?' })).not.toBeInTheDocument();
    await screen.findByRole('heading', { name: 'Second lesson' });
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/lessons/demo/files/lesson.md',
        expect.objectContaining({ method: 'PUT', body: JSON.stringify({ text: K_EDITED_SOURCE }) }),
      ),
    );
  });

  it('test_source_editor_given_unmounted_write_failure_expect_text_available_for_retry', async () => {
    let writes = 0;
    let serverSource = '# Demo source';
    const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'PUT') {
        writes += 1;
        if (writes === 1) return json({ error: 'write_failed' }, 500);
        serverSource = K_EDITED_SOURCE;
        return json({
          lesson: lessonPayload({
            lessonSource: serverSource,
            draft: { exists: true, startedFrom: 'digest', dirty: true, lastCommit: null },
          }),
        });
      }
      if (String(input) === '/api/curriculum') return json(curriculumPayload());
      return json(lessonPayload({ lessonSource: serverSource }));
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    await editSource();
    await userEvent.click(screen.getByRole('link', { name: 'Curriculum' }));
    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));

    await userEvent.click(screen.getByRole('link', { name: 'Lesson' }));
    await userEvent.click(await screen.findByRole('radio', { name: 'Source' }));

    expect(screen.getByRole('textbox', { name: 'Lesson source' })).toHaveValue(K_EDITED_SOURCE);
    await userEvent.click(screen.getByRole('button', { name: 'Retry draft write' }));
    await waitFor(() => expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(2));
    expect(fetch.mock.calls.filter(([, init]) => init?.method === 'PUT').at(-1)).toEqual([
      '/api/lessons/demo/files/lesson.md',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ text: K_EDITED_SOURCE }) }),
    ]);
  });
});
