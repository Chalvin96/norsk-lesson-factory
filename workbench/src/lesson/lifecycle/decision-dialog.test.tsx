import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { expect, it, vi } from 'vitest';
import { MarginNote } from '@/lesson/review/margin-note';
import { findingFixture } from '@/test/wire-fixtures';
import { KeepExerciseDialog } from './keep-exercise-dialog';

it('test_keep_given_keyboard_reason_and_empty_comment_expect_typed_payload', async () => {
  const onKeep = vi.fn();
  render(<KeepExerciseDialog exerciseLabel="Exercise · choose" onKeep={onKeep} />);
  await userEvent.click(screen.getByRole('button', { name: 'Keep as is' }));
  const group = screen.getByRole('radiogroup', { name: 'Reason' });
  const first = within(group).getByRole('radio', { name: 'Still matches the section' });
  expect(first).toBeChecked();
  first.focus();
  await userEvent.keyboard('{ArrowRight}');
  expect(within(group).getByRole('radio', { name: "Change doesn't affect it" })).toBeChecked();
  expect(screen.getByRole('textbox', { name: 'Comment (optional)' })).not.toBeRequired();
  await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Keep as is' }));
  expect(onKeep).toHaveBeenCalledWith({ reason: 'unaffected', comment: '' });
});

it('test_keep_comment_given_over_limit_input_expect_capped_text_and_counter', async () => {
  render(<KeepExerciseDialog exerciseLabel="Exercise" onKeep={vi.fn()} />);
  await userEvent.click(screen.getByRole('button', { name: 'Keep as is' }));
  const comment = screen.getByRole('textbox', { name: 'Comment (optional)' });
  expect(comment).toHaveAttribute('maxLength', '500');
  const user = userEvent.setup();
  await user.click(comment);
  await user.paste('x'.repeat(501));
  expect(comment).toHaveValue('x'.repeat(500));
  expect(screen.getByText('500 / 500')).toBeInTheDocument();
});

it('test_resolved_finding_given_legacy_record_expect_legacy_decision_label', () => {
  render(
    <MarginNote
      finding={findingFixture({
        findingId: 'legacy',
        target: { elementId: 'intro', blockIndex: 0 },
        origin: 'ai',
        severity: 'warning',
        status: 'resolved',
        code: 'wording',
        excerpt: '',
        explanation: 'Original wording issue',
        resolutionNote: 'Accepted wording',
      })}
    />,
  );
  expect(screen.getByText('Legacy decision')).toBeInTheDocument();
  expect(screen.getByText('Accepted wording')).toBeInTheDocument();
});
