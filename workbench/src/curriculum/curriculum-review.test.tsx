import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { curriculumPayload, renderAt } from '@/test/fixtures';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => vi.unstubAllGlobals());

describe('Curriculum review', () => {
  it('test_curriculum_review_save_given_pending_request_expect_save_actions_disabled', async () => {
    const data = curriculumPayload();
    const preview = {
      reviewDigest: 'review-current',
      entries: data.lessons.map((lesson) => ({
        sequenceIndex: lesson.sequenceIndex,
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
      dependencyFindings: [],
    };
    let finishSave: ((response: Response) => void) | undefined;
    const savePayloads: unknown[] = [];
    const pendingSave = new Promise<Response>((resolve) => {
      finishSave = resolve;
    });
    const fetch = vi.fn(async (path: string, init?: RequestInit) => {
      if (path === '/api/curriculum/preview') return json(preview);
      if (path === '/api/curriculum/save') {
        savePayloads.push(JSON.parse(String(init?.body)));
        return pendingSave;
      }
      return json({});
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/', { curriculum: data });

    screen.getByRole('button', { name: 'Open Third lesson details' }).focus();
    await userEvent.keyboard('{Alt>}{ArrowUp}{/Alt}');
    await userEvent.click(screen.getByRole('button', { name: 'Save curriculum' }));
    await screen.findByText('No dependency findings.');
    await userEvent.click(screen.getByRole('button', { name: 'Save curriculum' }));

    expect(await screen.findByRole('button', { name: 'Saving…' })).toBeDisabled();
    expect(fetch.mock.calls.filter(([path]) => path === '/api/curriculum/save')).toHaveLength(1);
    expect(savePayloads).toEqual([
      {
        edits: [{ kind: 'reorder', lesson_ids: ['demo', 'third', 'second'] }],
        review_digest: 'review-current',
      },
    ]);

    await act(async () => {
      finishSave?.(json({ commit: 'abc123', findings: [], curriculum: data }));
    });
  });
});
