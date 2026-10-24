import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import type { ExerciseAlignmentState } from '@/api/contracts';

import { ExerciseAlignmentNotice } from './exercise-alignment-notice';

const staleAlignment: ExerciseAlignmentState = {
  status: 'stale',
  exerciseKey: 'notice_name_question',
  changedSections: [
    {
      sectionId: 'a-first-meeting',
      beforeText: 'Hei! Jeg heter Mina. Hva heter du?',
      afterText: 'Hei! Jeg heter Mina.\nHva heter du?',
      summary: 'The question is now on its own line; its wording is unchanged.',
    },
  ],
  alignment: {
    exerciseKey: 'notice_name_question',
    checkedAgainst: { 'a-first-meeting': 'old-hash' },
    sectionSources: { 'a-first-meeting': 'Hei! Jeg heter Mina. Hva heter du?' },
    outcome: 'approved',
    reason: null,
    comment: null,
    note: null,
    at: '2026-10-02T00:00:00+00:00',
    decisionId: null,
  },
  history: [],
};

describe('ExerciseAlignmentNotice', () => {
  it('test_stale_exercise_alignment_given_changed_section_expect_reason_and_keep_dialog', async () => {
    const onKeep = vi.fn();
    render(
      <ExerciseAlignmentNotice
        alignment={staleAlignment}
        exerciseKey="notice_name_question"
        exerciseLabel="notice_name_question · choose"
        onKeep={onKeep}
      />,
    );

    expect(screen.getByText('Stale')).toBeInTheDocument();
    expect(screen.getByText(/question is now on its own line/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Keep as is' }));
    const dialog = screen.getByRole('dialog', { name: 'Keep exercise as is' });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Keep as is' }));

    expect(onKeep).toHaveBeenCalledWith('notice_name_question', { reason: 'still_matches', comment: '' });
  });

  it('test_untracked_exercise_alignment_given_empty_references_expect_alignment_badge', () => {
    render(
      <ExerciseAlignmentNotice
        alignment={{ status: 'untracked', exerciseKey: 'match_line_to_job', alignment: null, history: [] }}
        exerciseKey="match_line_to_job"
        exerciseLabel="match_line_to_job · match_pairs"
        onKeep={vi.fn()}
      />,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Alignment not tracked');
  });

  it('test_kept_exercise_alignment_given_owner_note_expect_history_line', () => {
    render(
      <ExerciseAlignmentNotice
        alignment={{
          status: 'aligned',
          exerciseKey: 'notice_name_question',
          alignment: {
            ...staleAlignment.alignment,
            checkedAgainst: { 'a-first-meeting': 'new-hash' },
            sectionSources: { 'a-first-meeting': 'New text' },
            outcome: 'kept',
            reason: null,
            comment: null,
            note: 'The question still asks for the same fact.',
            at: '2026-10-13T12:00:00+00:00',
          },
          history: [
            {
              ...staleAlignment.alignment,
              checkedAgainst: { 'a-first-meeting': 'new-hash' },
              sectionSources: { 'a-first-meeting': 'New text' },
              outcome: 'kept',
              reason: null,
              comment: null,
              note: 'The question still asks for the same fact.',
              at: '2026-10-13T12:00:00+00:00',
            },
          ],
        }}
        exerciseKey="notice_name_question"
        exerciseLabel="notice_name_question · choose"
        onKeep={vi.fn()}
      />,
    );

    expect(
      screen.getByText(/2026-10-13 · Kept · Legacy decision · The question still asks for the same fact./),
    ).toBeInTheDocument();
  });

  it('test_exercise_alignment_history_given_multiple_decisions_expect_newest_first_with_decisions_and_notes', () => {
    const olderApproval = {
      ...staleAlignment.alignment,
      outcome: 'approved' as const,
      reason: null,
      comment: null,
      note: 'Reviewed against the original wording.',
      at: '2026-10-11T09:00:00+00:00',
    };
    const newerKeep = {
      ...staleAlignment.alignment,
      outcome: 'kept' as const,
      reason: null,
      comment: null,
      note: 'The question still asks for the same fact.',
      at: '2026-10-13T12:00:00+00:00',
    };

    render(
      <ExerciseAlignmentNotice
        alignment={{
          status: 'aligned',
          exerciseKey: 'notice_name_question',
          alignment: newerKeep,
          history: [olderApproval, newerKeep],
        }}
        exerciseKey="notice_name_question"
        exerciseLabel="notice_name_question · choose"
        onKeep={vi.fn()}
      />,
    );

    const history = screen.getByRole('region', { name: 'Exercise alignment history' });
    const entries = within(history).getAllByRole('listitem');
    expect(entries).toHaveLength(2);
    expect(entries[0]).toHaveTextContent(
      '2026-10-13 · Kept · Legacy decision · The question still asks for the same fact.',
    );
    expect(entries[1]).toHaveTextContent('2026-10-11 · Approved · Reviewed against the original wording.');
  });
});
