import { screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PacketElement } from '@/api/contracts';
import { lessonPayload, renderAt, rowElement } from '@/test/fixtures';

function lessonWithBlocks(blocks: Record<string, unknown>[]) {
  const lesson = lessonPayload();
  const section: PacketElement = {
    kind: 'section',
    id: 'intro',
    anchor_id: 'intro',
    title: 'Intro',
    role: 'orient',
    blocks,
  };
  return {
    ...lesson,
    elements: [section],
    state: { ...lesson.state, findings: [], proposals: [] },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.removeItem('regeneration-demo');
});

describe('Lesson document', () => {
  it('test_lesson_rows_given_findings_and_proposal_expect_notes_and_cards_on_target_rows', () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    expect(within(rowElement('intro-b1')).getByText('Gloss note')).toBeInTheDocument();
    expect(rowElement('intro-b1')).toHaveAttribute('data-flag', 'warning');
    expect(within(rowElement('ex1')).getByText('Check note')).toBeInTheDocument();
    expect(rowElement('ex1')).toHaveAttribute('data-flag', 'error');
    expect(within(rowElement('intro-b0')).getByText('After text')).toBeInTheDocument();
    expect(within(screen.getByRole('group', { name: 'Open findings' })).getByText(/^2/)).toBeInTheDocument();
    expect(screen.getByText('Lesson: Open proposal')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Approve' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Approve' })).toHaveAccessibleDescription(/Lesson: Open proposal/);
  });

  it('test_reading_row_given_edit_control_expect_control_anchored_inside_block_body', () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    const body = within(rowElement('intro-b0')).getByRole('group', { name: 'Block body' });
    expect(within(body).getByLabelText('Block actions')).toBeInTheDocument();
    expect(within(body).getByRole('button', { name: 'Edit block' })).toBeInTheDocument();
  });

  it('test_plain_block_given_edit_control_expect_text_reserves_action_space', () => {
    const lesson = lessonWithBlocks([
      { kind: 'paragraph', spans: [{ kind: 'text', value: 'A paragraph beside its edit control.' }] },
    ]);
    renderAt('/lessons/demo', { lesson });

    expect(within(rowElement('intro-b0')).getByRole('group', { name: 'Block body' })).toContainElement(
      screen.getByText('A paragraph beside its edit control.'),
    );
  });

  it('test_exercise_row_given_edit_control_expect_objective_text_leaves_action_space', () => {
    const lesson = lessonPayload();
    const exercise: PacketElement = {
      kind: 'exercise',
      id: 'exercise-one',
      anchor_id: 'exercise-one',
      operation: 'choose',
      objective_id: 'objective-id-that-would-overlap-the-edit-action',
      prompt: [{ kind: 'text', value: 'Choose a response.' }],
      payload: { options: [] },
    };
    renderAt('/lessons/demo', {
      lesson: { ...lesson, elements: [exercise], state: { ...lesson.state, findings: [], proposals: [] } },
    });

    expect(within(rowElement('exercise-one')).getByRole('group', { name: 'Exercise body' })).toContainElement(
      screen.getByText('objective-id-that-would-overlap-the-edit-action', { selector: 'span' }),
    );
  });
});
