import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { curriculumPayload, renderAt } from '@/test/fixtures';
import { chatTurnFixture } from '@/test/wire-fixtures';

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => vi.unstubAllGlobals());

describe('Curriculum edits', () => {
  it('test_add_entry_prerequisites_given_comma_separated_input_expect_raw_text_preserved', async () => {
    let previewBody: unknown;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (path: string, init?: RequestInit) => {
        if (path === '/api/curriculum/preview') {
          previewBody = JSON.parse(String(init?.body));
          return json({
            reviewDigest: 'preview',
            entries: [],
            affectedLessons: [],
            movedOutcomes: [],
            movedPrerequisites: [],
            dependencyFindings: [],
          });
        }
        return json({});
      }),
    );
    renderAt('/');

    await userEvent.click(screen.getByRole('button', { name: 'Add entry' }));
    const prerequisites = await screen.findByLabelText('Required prerequisites (comma-separated IDs)');
    await userEvent.type(prerequisites, 'demo,second');

    expect(prerequisites).toHaveValue('demo,second');
    await waitFor(() =>
      expect(previewBody).toMatchObject({
        edits: [
          {
            entry: { prerequisites: ['demo', 'second'] },
          },
        ],
      }),
    );
  });

  it('test_entry_editor_given_cancel_then_save_expect_cancel_discards_and_save_updates_title', async () => {
    const data = curriculumPayload();
    const updated = {
      ...data,
      lessons: data.lessons.map((lesson) =>
        lesson.lessonId === 'demo' ? { ...lesson, title: 'Updated title' } : lesson,
      ),
    };
    const preview = {
      reviewDigest: 'review-digest',
      entries: updated.lessons.map((lesson) => ({
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
        : json({ commit: 'abc123', findings: [], curriculum: { ...updated, curriculumApproval: 'human_approved' } }),
    );
    vi.stubGlobal('fetch', fetch);
    renderAt('/');
    await userEvent.click(screen.getByRole('button', { name: 'Edit entry' }));
    expect(screen.getByRole('button', { name: 'Cancel' })).toHaveClass('bg-background', 'border-input');
    await userEvent.clear(screen.getByLabelText('Title'));
    await userEvent.type(screen.getByLabelText('Title'), 'Discarded');
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(fetch).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Edit entry' }));
    expect(screen.getByLabelText('Title')).toHaveValue('Demo lesson');
    await userEvent.clear(screen.getByLabelText('Title'));
    await userEvent.type(screen.getByLabelText('Title'), 'Updated title');
    await userEvent.click(screen.getByRole('button', { name: 'Preview changes' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Accept edit' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: 'Accept edit' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save curriculum' }));
    await userEvent.click(await screen.findByLabelText(/I reviewed the current curriculum content/));
    await userEvent.click(screen.getByRole('button', { name: 'Save reviewed curriculum' }));
    expect(await screen.findByRole('heading', { name: 'Updated title' })).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      '/api/curriculum/save',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          edits: [
            {
              kind: 'update',
              lesson_id: 'demo',
              title: 'Updated title',
              learner_outcome: 'Use the skill.',
              cefr_level: 'A1',
              prerequisites: [],
              helpful_prerequisites: [],
            },
          ],
          review_digest: 'review-digest',
          finding_decisions: [],
          force: false,
        }),
      }),
    );
  });

  it('test_chat_edit_given_valid_suggestion_expect_same_preview_and_staged_edit', async () => {
    const data = curriculumPayload();
    data.chat = [
      chatTurnFixture({ role: 'author', text: 'Move Third lesson before Second lesson', kind: 'curriculum' }),
      chatTurnFixture({
        role: 'assistant',
        text: 'Move the third lesson up.',
        proposedEdit: { kind: 'reorder', lessonIds: ['demo', 'third', 'second'] },
        kind: 'curriculum',
      }),
    ];
    const preview = {
      reviewDigest: 'review-digest',
      entries: [data.lessons[0], data.lessons[2], data.lessons[1]].map((lesson, sequenceIndex) => ({
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
      dependencyFindings: [],
    };
    const fetch = vi.fn(async () => json(preview));
    vi.stubGlobal('fetch', fetch);
    renderAt('/', { curriculum: data });
    await userEvent.click(screen.getByRole('button', { name: 'Open in reorder preview' }));
    expect(await screen.findByRole('heading', { name: 'This edit' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Accept edit' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: 'Accept edit' }));
    expect(
      Array.from(document.querySelectorAll('[data-entry-id]')).map((item) => item.getAttribute('data-entry-id')),
    ).toEqual(['demo', 'third', 'second']);
    expect(screen.getByRole('button', { name: 'Save curriculum' })).toBeEnabled();
    expect(fetch).toHaveBeenCalledWith('/api/curriculum/preview', expect.objectContaining({ method: 'POST' }));
  });

  it('test_merge_entries_given_two_selected_lessons_expect_reviewable_merge_preview', async () => {
    const data = curriculumPayload();
    const preview = {
      reviewDigest: 'review-digest',
      entries: [
        {
          sequenceIndex: 0,
          catalogId: 'demo',
          title: 'Merged entries',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A1',
          prerequisites: [],
          helpfulPrerequisites: [],
        },
      ],
      affectedLessons: [
        { lessonId: 'demo', title: 'Merged entries', effect: 'needs_replan', detail: 'Re-plan this lesson.' },
      ],
      movedOutcomes: [],
      movedPrerequisites: [],
      dependencyFindings: [],
    };
    const fetch = vi.fn(async () => json(preview));
    vi.stubGlobal('fetch', fetch);
    renderAt('/', { curriculum: data });
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Demo lesson' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Second lesson' }));
    await userEvent.click(screen.getByRole('button', { name: 'Merge…' }));
    expect(await screen.findByRole('heading', { name: 'Merge entries' })).toBeInTheDocument();
    expect(
      screen.getByText(
        (_, element) =>
          element?.textContent === 'Merging 2 entries into one. Keeps ID demo (position 01) in authored order.',
      ),
    ).toBeInTheDocument();
    expect(await screen.findByText(/Lessons affected/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Accept merge' })).toBeEnabled();
    expect(fetch).toHaveBeenCalledWith('/api/curriculum/preview', expect.objectContaining({ method: 'POST' }));
  });

  it('test_curriculum_edit_preview_given_prior_staged_merge_expect_current_and_cumulative_impacts_separated', async () => {
    const data = curriculumPayload();
    const mergePreview = {
      reviewDigest: 'merge-review',
      entries: [
        {
          sequenceIndex: 0,
          catalogId: 'demo',
          title: 'Merged entries',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A1',
          prerequisites: [],
          helpfulPrerequisites: [],
        },
        {
          sequenceIndex: 1,
          catalogId: 'third',
          title: 'Third lesson',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A2',
          prerequisites: ['demo'],
          helpfulPrerequisites: [],
        },
      ],
      affectedLessons: [
        {
          lessonId: 'demo',
          title: 'Merged entries',
          effect: 'needs_replan' as const,
          detail: 'The staged merge changes this lesson.',
        },
      ],
      movedOutcomes: [],
      movedPrerequisites: [],
      dependencyFindings: [],
    };
    const removePreview = {
      reviewDigest: 'remove-only-review',
      entries: [
        {
          sequenceIndex: 0,
          catalogId: 'second',
          title: 'Second lesson',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A1',
          prerequisites: ['demo'],
          helpfulPrerequisites: [],
        },
        {
          sequenceIndex: 1,
          catalogId: 'third',
          title: 'Third lesson',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A2',
          prerequisites: ['second'],
          helpfulPrerequisites: [],
        },
      ],
      affectedLessons: [
        {
          lessonId: 'demo',
          title: 'Demo lesson',
          effect: 'unlisted' as const,
          detail: 'This edit removes Demo lesson.',
        },
      ],
      movedOutcomes: [],
      movedPrerequisites: [],
      dependencyFindings: [],
    };
    const cumulativePreview = {
      reviewDigest: 'merge-and-remove-review',
      entries: [
        {
          sequenceIndex: 0,
          catalogId: 'third',
          title: 'Third lesson',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A2',
          prerequisites: [],
          helpfulPrerequisites: [],
        },
      ],
      affectedLessons: [
        {
          lessonId: 'third',
          title: 'Third lesson',
          effect: 'needs_replan' as const,
          detail: 'Cumulative impact after the staged merge and this removal.',
        },
      ],
      movedOutcomes: [],
      movedPrerequisites: [],
      dependencyFindings: [],
    };
    const requests: { edits: { kind: string }[] }[] = [];
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (path !== '/api/curriculum/preview') return json(data);
      const payload = JSON.parse(String(options?.body)) as { edits: { kind: string }[] };
      requests.push(payload);
      const kinds = payload.edits.map((edit) => edit.kind);
      const result =
        kinds.length === 1 && kinds[0] === 'merge'
          ? mergePreview
          : kinds.length === 1
            ? removePreview
            : cumulativePreview;
      return json(result);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/', { curriculum: data });

    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Demo lesson' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Second lesson' }));
    await userEvent.click(screen.getByRole('button', { name: 'Merge…' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Accept merge' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: 'Accept merge' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Merged entries' }));
    await userEvent.click(screen.getByRole('button', { name: 'Remove…' }));

    expect(await screen.findByRole('heading', { name: 'This edit' })).toBeInTheDocument();
    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByRole('region', { name: 'This edit impact' }).textContent).toContain(
      'Cumulative impact after the staged merge and this removal.',
    );
    const stagedDetails = screen.getByText('All staged edits (2)').closest('details');
    if (!stagedDetails) throw new Error('All staged edits disclosure is missing.');
    expect(stagedDetails).not.toHaveAttribute('open');
    await userEvent.click(within(stagedDetails).getByText('All staged edits (2)'));
    expect(within(stagedDetails).getByText(/Cumulative impact after the staged merge/)).toBeInTheDocument();
    expect(requests.slice(-2)).toEqual([
      expect.objectContaining({ edits: [expect.objectContaining({ kind: 'merge' })] }),
      expect.objectContaining({
        edits: [expect.objectContaining({ kind: 'merge' }), expect.objectContaining({ kind: 'remove' })],
      }),
    ]);

    await waitFor(() => expect(screen.getByRole('button', { name: 'Accept removal' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: 'Accept removal' }));
    expect(
      Array.from(document.querySelectorAll('[data-entry-id]')).map((item) => item.getAttribute('data-entry-id')),
    ).toEqual(['third']);
  });

  it('test_remove_staged_add_given_new_entry_selected_expect_cumulative_preview_enables_accept', async () => {
    const data = curriculumPayload();
    const addedPreview = {
      reviewDigest: 'add-review',
      entries: [
        {
          sequenceIndex: 0,
          catalogId: 'demo',
          title: 'Demo lesson',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A1',
          prerequisites: [],
          helpfulPrerequisites: [],
        },
        {
          sequenceIndex: 1,
          catalogId: 'new_topic',
          title: 'New topic',
          learnerOutcome: 'Talk about a new topic.',
          cefrLevel: 'A1',
          prerequisites: [],
          helpfulPrerequisites: [],
        },
        {
          sequenceIndex: 2,
          catalogId: 'second',
          title: 'Second lesson',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A1',
          prerequisites: ['demo'],
          helpfulPrerequisites: [],
        },
        {
          sequenceIndex: 3,
          catalogId: 'third',
          title: 'Third lesson',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A2',
          prerequisites: ['second'],
          helpfulPrerequisites: [],
        },
      ],
      affectedLessons: [
        {
          lessonId: 'new_topic',
          title: 'New topic',
          effect: 'added' as const,
          detail: 'This entry is added to the curriculum.',
        },
      ],
      movedOutcomes: [],
      movedPrerequisites: [],
      dependencyFindings: [],
    };
    const removedPreview = {
      reviewDigest: 'add-and-remove-review',
      entries: data.lessons.map((lesson, sequenceIndex) => ({
        sequenceIndex,
        catalogId: lesson.lessonId,
        title: lesson.title,
        learnerOutcome: data.entries[lesson.lessonId].learnerOutcome,
        cefrLevel: lesson.cefrLevel,
        prerequisites: data.entries[lesson.lessonId].dependsOn,
        helpfulPrerequisites: [],
      })),
      affectedLessons: [
        {
          lessonId: 'new_topic',
          title: 'New topic',
          effect: 'unlisted' as const,
          detail: 'This entry leaves the curriculum.',
        },
      ],
      movedOutcomes: [],
      movedPrerequisites: [],
      dependencyFindings: [],
    };
    const requests: { edits: { kind: string; lesson_id?: string; entry?: { catalog_id: string } }[] }[] = [];
    const fetch = vi.fn(async (path: string, options?: RequestInit) => {
      if (path !== '/api/curriculum/preview') return json(data);
      const payload = JSON.parse(String(options?.body)) as {
        edits: { kind: string; lesson_id?: string; entry?: { catalog_id: string } }[];
      };
      requests.push(payload);
      const kinds = payload.edits.map((edit) => edit.kind);
      if (kinds.length === 1 && kinds[0] === 'remove')
        return json({ error: 'new_topic is not in the current curriculum' }, 422);
      return json(kinds.includes('remove') ? removedPreview : addedPreview);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/', { curriculum: data });

    await userEvent.click(screen.getByRole('button', { name: 'Add entry' }));
    const lessonId = await screen.findByLabelText('Lesson ID');
    await userEvent.clear(lessonId);
    await userEvent.type(lessonId, 'new_topic');
    await userEvent.type(await screen.findByLabelText('Title'), 'New topic');
    await userEvent.type(await screen.findByLabelText('Learner outcome'), 'Talk about a new topic.');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Accept edit' })).toBeEnabled());
    await userEvent.click(await screen.findByRole('button', { name: 'Accept edit' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select New topic' }));
    await userEvent.click(screen.getByRole('button', { name: 'Remove…' }));

    expect(await screen.findByRole('heading', { name: 'Remove entries' })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Accept removal' })).toBeEnabled());
    expect(requests.slice(-2).map(({ edits }) => edits.map(({ kind }) => kind))).toEqual([['add'], ['add', 'remove']]);
  });

  it('test_split_entry_given_two_authored_parts_expect_preview_before_accept', async () => {
    const data = curriculumPayload();
    const preview = {
      reviewDigest: 'split-review',
      entries: [
        {
          sequenceIndex: 0,
          catalogId: 'demo',
          title: 'Greetings',
          learnerOutcome: 'Greet someone.',
          cefrLevel: 'A1',
          prerequisites: [],
          helpfulPrerequisites: [],
        },
        {
          sequenceIndex: 1,
          catalogId: 'demo_part_2',
          title: 'Introductions',
          learnerOutcome: 'Introduce yourself.',
          cefrLevel: 'A1',
          prerequisites: ['demo'],
          helpfulPrerequisites: [],
        },
        {
          sequenceIndex: 2,
          catalogId: 'second',
          title: 'Second lesson',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A1',
          prerequisites: ['demo_part_2'],
          helpfulPrerequisites: [],
        },
        {
          sequenceIndex: 3,
          catalogId: 'third',
          title: 'Third lesson',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A2',
          prerequisites: ['second'],
          helpfulPrerequisites: [],
        },
      ],
      affectedLessons: [],
      movedOutcomes: [],
      movedPrerequisites: [],
      dependencyFindings: [],
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json(preview)),
    );
    renderAt('/', { curriculum: data });
    await userEvent.click(screen.getByRole('button', { name: 'More actions for Demo lesson' }));
    await userEvent.click(await screen.findByRole('menuitem', { name: 'Split entry' }));
    await userEvent.clear(screen.getByLabelText('Part 2 title'));
    await userEvent.type(screen.getByLabelText('Part 2 title'), 'Introductions');
    await userEvent.type(screen.getByLabelText('Part 2 learner outcome'), 'Introduce yourself.');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Accept split' })).toBeEnabled());
  });

  it('test_remove_entry_given_selected_lesson_expect_package_disposition_preview', async () => {
    const data = curriculumPayload();
    const preview = {
      reviewDigest: 'remove-review',
      entries: data.lessons.slice(1).map((lesson, index) => ({
        sequenceIndex: index,
        catalogId: lesson.lessonId,
        title: lesson.title,
        learnerOutcome: data.entries[lesson.lessonId].learnerOutcome,
        cefrLevel: lesson.cefrLevel,
        prerequisites: data.entries[lesson.lessonId].dependsOn,
        helpfulPrerequisites: [],
      })),
      affectedLessons: [
        {
          lessonId: 'demo',
          title: 'Demo lesson',
          effect: 'unlisted' as const,
          detail: 'The lesson package stays in the workspace.',
        },
      ],
      movedOutcomes: [],
      movedPrerequisites: [],
      dependencyFindings: [],
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json(preview)),
    );
    renderAt('/', { curriculum: data });
    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Demo lesson' }));
    await userEvent.click(screen.getByRole('button', { name: 'Remove…' }));
    expect(await screen.findByText(/Lesson packages stay in the workspace/)).toBeInTheDocument();
    expect(screen.getByText('Demo lesson · canonical package')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Accept removal' })).toBeEnabled());
  });
});
