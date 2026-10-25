import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { curriculumPayload, renderAt } from '@/test/fixtures';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => vi.unstubAllGlobals());

describe('Curriculum order', () => {
  it('test_order_save_given_reordered_entries_expect_full_permutation_and_committed_state', async () => {
    const data = curriculumPayload();
    const updated = { ...data, lessons: [data.lessons[0], data.lessons[2], data.lessons[1]] };
    const preview = {
      reviewDigest: 'review-digest',
      entries: updated.lessons.map((lesson, sequenceIndex) => ({
        sequenceIndex,
        catalogId: lesson.lessonId,
        title: lesson.title,
        learnerOutcome: data.entries[lesson.lessonId].learnerOutcome,
        cefrLevel: lesson.cefrLevel,
        prerequisites: data.entries[lesson.lessonId].dependsOn,
        helpfulPrerequisites: [],
      })),
      affectedLessons: [],
      movedOutcomes: [],
      movedPrerequisites: [],
      dependencyFindings: [
        {
          findingId: 'third-second',
          lessonId: 'third',
          prerequisiteId: 'second',
          code: 'curriculum.prerequisite_order' as const,
          explanation: 'Third lesson appears before Second lesson.',
        },
      ],
    };
    const fetch = vi.fn(async (path: string) =>
      path === '/api/curriculum/preview'
        ? json(preview)
        : json({
            commit: 'abcdef012345',
            findings: [],
            curriculum: { ...updated },
          }),
    );
    vi.stubGlobal('fetch', fetch);
    renderAt('/');
    screen.getByRole('button', { name: 'Open Third lesson details' }).focus();
    await userEvent.keyboard('{Alt>}{ArrowUp}{/Alt}');
    await userEvent.click(screen.getByRole('button', { name: 'Save curriculum' }));
    await screen.findByText('Third lesson appears before Second lesson.');
    await userEvent.click(screen.getByRole('button', { name: 'Save curriculum' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(fetch).toHaveBeenCalledWith(
      '/api/curriculum/save',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          edits: [{ kind: 'reorder', lesson_ids: ['demo', 'third', 'second'] }],
          review_digest: 'review-digest',
        }),
      }),
    );
    expect(screen.getByText('Order saved')).toBeInTheDocument();
  });

  it('test_order_save_given_server_refusal_expect_order_kept_unsaved_and_error_visible', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string) =>
        path === '/api/curriculum/preview'
          ? json({
              reviewDigest: 'review-digest',
              entries: [],
              affectedLessons: [],
              movedOutcomes: [],
              movedPrerequisites: [],
              dependencyFindings: [],
            })
          : json({ error: 'Save requires a working branch' }, 409),
      ),
    );
    renderAt('/');
    screen.getByRole('button', { name: 'Open Third lesson details' }).focus();
    await userEvent.keyboard('{Alt>}{ArrowUp}{/Alt}');
    await userEvent.click(screen.getByRole('button', { name: 'Save curriculum' }));
    await screen.findByRole('heading', { name: 'Save curriculum changes' });
    await userEvent.click(await screen.findByRole('button', { name: 'Save curriculum' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Save requires a working branch');
    expect(screen.getByText('Unsaved edits')).toBeInTheDocument();
  });
});
