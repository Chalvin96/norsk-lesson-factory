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
    data.curriculumApproval = 'pending_human';
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

    await userEvent.click(screen.getByRole('button', { name: 'Review curriculum' }));
    await screen.findByText('No dependency findings.');
    await userEvent.click(screen.getByLabelText(/I reviewed the current curriculum content/));
    await userEvent.click(screen.getByRole('button', { name: 'Save reviewed curriculum' }));

    expect(await screen.findByRole('button', { name: 'Saving…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Force save unreviewed' })).toBeDisabled();
    expect(fetch.mock.calls.filter(([path]) => path === '/api/curriculum/save')).toHaveLength(1);
    expect(savePayloads).toEqual([
      {
        edits: [{ kind: 'reorder', lesson_ids: ['demo', 'second', 'third'] }],
        review_digest: 'review-current',
        finding_decisions: [],
        force: false,
      },
    ]);

    await act(async () => {
      finishSave?.(json({ commit: 'abc123', findings: [], curriculum: data }));
    });
  });

  it('test_pending_review_given_no_new_edits_expect_current_curriculum_reviewable', async () => {
    const data = curriculumPayload();
    data.curriculumApproval = 'pending_human';
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
    const fetch = vi.fn(async (path: string) =>
      path === '/api/curriculum/preview'
        ? json(preview)
        : json({ commit: 'abc123', findings: [], curriculum: { ...data, curriculumApproval: 'human_approved' } }),
    );
    vi.stubGlobal('fetch', fetch);
    renderAt('/', { curriculum: data });

    await userEvent.click(screen.getByRole('button', { name: 'Review curriculum' }));
    await userEvent.click(await screen.findByLabelText(/I reviewed the current curriculum content/));
    await userEvent.click(screen.getByRole('button', { name: 'Save reviewed curriculum' }));

    expect(fetch).toHaveBeenCalledWith(
      '/api/curriculum/preview',
      expect.objectContaining({
        body: JSON.stringify({ edits: [{ kind: 'reorder', lesson_ids: ['demo', 'second', 'third'] }] }),
      }),
    );
    expect(fetch).toHaveBeenCalledWith(
      '/api/curriculum/save',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          edits: [{ kind: 'reorder', lesson_ids: ['demo', 'second', 'third'] }],
          review_digest: 'review-current',
          finding_decisions: [],
          force: false,
        }),
      }),
    );
  });
});
