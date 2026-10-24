import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import type { WorkbenchFinding } from '@/api/contracts';
import { KeepExerciseDialog } from '@/lesson/lifecycle/keep-exercise-dialog';
import { MarginNote } from '@/lesson/review/margin-note';
import { ProposalCard } from '@/lesson/review/proposal-card';
import { findingFixture, proposalFixture } from '@/test/wire-fixtures';
import { ContextChip } from './context-chip';
import { PaneHeader } from './pane-header';
import { SegmentedControl } from './segmented-control';
import { StatusChip } from './status-chip';

const openFinding: WorkbenchFinding = findingFixture({
  findingId: 'f1',
  target: { elementId: 'intro', blockIndex: 0 },
  origin: 'ai',
  severity: 'warning',
  status: 'open',
  code: 'ambiguous-gloss',
  excerpt: 'Jeg lager kaffe nå.',
  explanation: 'The gloss could be read two ways.',
  advisory: false,
});

describe('MarginNote', () => {
  it('test_margin_note_given_multiple_issues_expect_preserved_line_breaks', () => {
    const message = 'Ambiguous target: Two forms fit.\nAnswer leak: The source supplies the answer.';
    render(<MarginNote finding={{ ...openFinding, message }} />);

    expect(screen.getByText('Ambiguous target:', { exact: false }).textContent).toBe(message);
  });

  it('test_margin_note_given_exercise_review_expect_reason_before_evidence_and_raw_details', () => {
    const reason = 'Supplied answer retrieval: The source supplies the answer.';
    render(
      <MarginNote
        finding={{ ...openFinding, message: reason, excerpt: 'Jeg jobber her.', details: '{"issues": []}' }}
      />,
    );

    const message = screen.getByText(reason);
    const evidence = screen.getByText('Jeg jobber her.');
    expect(message.compareDocumentPosition(evidence) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText('Details')).toBeInTheDocument();
  });

  it('test_margin_note_actions_given_open_finding_expect_callbacks_receive_the_finding', async () => {
    const onAskChat = vi.fn();
    const onResolve = vi.fn();
    render(<MarginNote finding={openFinding} onAskChat={onAskChat} onResolve={onResolve} />);

    await userEvent.click(screen.getByRole('button', { name: 'Ask chat' }));
    await userEvent.click(screen.getByRole('button', { name: 'Resolve' }));

    expect(onAskChat).toHaveBeenCalledWith(openFinding);
    expect(onResolve).toHaveBeenCalledWith(openFinding);
  });

  it('test_margin_note_given_resolved_finding_expect_no_resolve_action_and_resolved_label', () => {
    render(<MarginNote finding={{ ...openFinding, status: 'resolved' }} onResolve={vi.fn()} />);

    expect(screen.queryByRole('button', { name: 'Resolve' })).toBeNull();
    expect(screen.getByText('Resolved')).toBeInTheDocument();
  });

  it('test_margin_note_given_open_advisory_finding_expect_fix_action_and_unverified_label', () => {
    const onFix = vi.fn();
    render(<MarginNote finding={{ ...openFinding, advisory: true }} onFix={onFix} onResolve={vi.fn()} />);

    expect(screen.getByText('AI suggestion · unverified')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Fix' })).toBeInTheDocument();
  });

  it('test_margin_note_given_second_opinion_finding_expect_advisory_reviewer_label', () => {
    render(<MarginNote finding={{ ...openFinding, advisory: true, reviewer: 'second_opinion' }} />);

    expect(screen.getByText('Second opinion')).toBeInTheDocument();
    expect(screen.getByText('AI suggestion · unverified')).toBeInTheDocument();
  });

  it('test_margin_note_given_check_origin_expect_check_label_and_named_article', () => {
    render(<MarginNote finding={{ ...openFinding, origin: 'check', code: 'exercise-stale-reference' }} />);

    expect(screen.getByRole('article', { name: 'Check finding Review finding' })).toBeInTheDocument();
    expect(screen.getByText('Check')).toBeInTheDocument();
  });

  it('test_margin_note_given_untrusted_finding_text_expect_text_not_markup', () => {
    const hostile = '<img src=x onerror=alert(1)>';
    const { container } = render(
      <MarginNote finding={{ ...openFinding, code: hostile, explanation: hostile, details: hostile }} />,
    );

    expect(container.querySelector('img')).toBeNull();
    expect(screen.getAllByText(hostile)).toHaveLength(2);
  });
});

describe('ProposalCard', () => {
  it('test_proposal_card_actions_given_pending_proposal_expect_accept_and_reject_callbacks', async () => {
    const allowedDecisions: ('accept' | 'reject' | 'keep')[] = ['accept', 'reject'];
    const proposal = proposalFixture({
      proposalId: 'p1',
      before: 'Før.',
      after: 'Etter.',
      note: 'Suggested edit.',
      allowedDecisions,
    });
    const onAccept = vi.fn();
    const onReject = vi.fn();
    render(<ProposalCard proposal={proposal} onAccept={onAccept} onReject={onReject} />);

    await userEvent.click(screen.getByRole('button', { name: 'Accept' }));
    await userEvent.click(screen.getByRole('button', { name: 'Reject' }));

    expect(onAccept).toHaveBeenCalledWith(proposal);
    expect(onReject).toHaveBeenCalledWith(proposal);
  });

  it('test_regeneration_proposal_given_decision_support_expect_english_fields_and_keep_action', async () => {
    const proposal = proposalFixture({
      proposalId: 'p1',
      origin: 'regenerate' as const,
      allowedDecisions: ['accept', 'reject', 'keep'] as ('accept' | 'reject' | 'keep')[],
      before: 'Før.',
      after: 'Etter.',
      note: 'Minimal change',
      changeSummary: 'Only the explanation now quotes the shorter section line.',
      translations: [{ norwegian: 'Hva heter du?', english: 'What is your name?' }],
      answerImpact: 'none',
      uncertainty: null,
      changedSections: [
        {
          sectionId: 'a-first-meeting',
          beforeText: 'Old line.',
          afterText: 'New line.',
          summary: 'The greeting is now split across two lines.',
        },
      ],
    });
    const onKeepAsIs = vi.fn();
    render(<ProposalCard proposal={proposal} onKeepAsIs={(_proposal, note) => onKeepAsIs(note)} />);

    expect(screen.getByText(proposal.changeSummary)).toBeInTheDocument();
    expect(screen.getByText(proposal.changedSections[0].summary).closest('[lang="en"]')).toBeInTheDocument();
    expect(screen.getByText('Hva heter du?')).toHaveAttribute('lang', 'nb');
    expect(screen.getByText('What is your name?')).toHaveAttribute('lang', 'en');
    expect(screen.getByText('None stated by the generator.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Keep as is' }));
    const dialog = screen.getByRole('dialog', { name: 'Keep exercise as is' });
    await userEvent.click(within(dialog).getByRole('button', { name: 'Keep as is' }));

    expect(onKeepAsIs).toHaveBeenCalledWith({ reason: 'still_matches', comment: '' });
  });

  it('test_proposal_card_given_chat_proposal_with_change_summary_and_keep_handler_expect_no_keep_as_is_button', () => {
    render(
      <ProposalCard
        proposal={proposalFixture({
          proposalId: 'p1',
          origin: 'chat',
          allowedDecisions: ['accept', 'reject'],
          before: 'Før.',
          after: 'Etter.',
          note: 'Warmer gloss.',
          changeSummary: 'Gives the greeting a warmer English gloss.',
        })}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        onKeepAsIs={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Accept' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Keep as is' })).toBeNull();
  });

  it('test_proposal_card_given_regeneration_proposal_with_keep_handler_expect_keep_as_is_button', () => {
    render(
      <ProposalCard
        proposal={proposalFixture({
          proposalId: 'p1',
          origin: 'regenerate',
          allowedDecisions: ['accept', 'reject', 'keep'],
          before: 'Før.',
          after: 'Etter.',
          note: 'Stale exercise regenerated.',
          changeSummary: 'Only the explanation now quotes the shorter section line.',
        })}
        onAccept={vi.fn()}
        onReject={vi.fn()}
        onKeepAsIs={vi.fn()}
      />,
    );

    expect(screen.getByRole('button', { name: 'Keep as is' })).toBeInTheDocument();
  });

  it('test_proposal_card_why_given_chat_proposal_with_change_summary_expect_summary_without_regeneration_claim', () => {
    const summary = 'Gives the greeting a warmer English gloss.';
    render(
      <ProposalCard
        proposal={proposalFixture({
          proposalId: 'p1',
          before: 'Før.',
          after: 'Etter.',
          note: 'Warmer gloss.',
          origin: 'chat',
          allowedDecisions: ['accept', 'reject'],
          changeSummary: summary,
          changedSections: [],
        })}
      />,
    );

    const panel = screen.getByLabelText('Decision support');
    expect(within(panel).queryByText('The exercise was regenerated against its changed section.')).toBeNull();
    expect(within(panel).getAllByText(summary)).toHaveLength(2);
  });

  it('test_proposal_card_why_given_regeneration_proposal_without_section_changes_expect_regeneration_fallback', () => {
    render(
      <ProposalCard
        proposal={proposalFixture({
          proposalId: 'p1',
          before: 'Før.',
          after: 'Etter.',
          note: 'Regenerated exercise',
          origin: 'regenerate',
          allowedDecisions: ['accept', 'reject', 'keep'],
          changeSummary: 'Only the explanation changed.',
          changedSections: [],
        })}
      />,
    );

    const panel = screen.getByLabelText('Regeneration decision support');
    expect(within(panel).getByText('The exercise was regenerated against its changed section.')).toBeInTheDocument();
  });

  it('test_proposal_card_given_non_exercise_regeneration_expect_change_summary_without_keep_action', () => {
    const summary = 'The reading block was regenerated.';
    render(
      <ProposalCard
        proposal={proposalFixture({
          proposalId: 'p1',
          before: 'Før.',
          after: 'Etter.',
          note: 'Updated reading.',
          origin: 'regenerate',
          allowedDecisions: ['accept', 'reject'],
          changeSummary: summary,
          changedSections: [],
        })}
        onKeepAsIs={vi.fn()}
      />,
    );

    const panel = screen.getByLabelText('Regeneration decision support');
    expect(within(panel).queryByText('The exercise was regenerated against its changed section.')).toBeNull();
    expect(within(panel).getAllByText(summary)).toHaveLength(2);
    expect(screen.queryByRole('button', { name: 'Keep as is' })).toBeNull();
  });

  it('test_proposal_card_given_settled_proposal_expect_verdict_instead_of_actions', () => {
    render(
      <ProposalCard
        proposal={proposalFixture({
          proposalId: 'p1',
          before: 'a',
          after: 'b',
          note: 'n',
          allowedDecisions: ['accept', 'reject'],
        })}
        verdict="accepted"
      />,
    );

    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText('Accepted')).toBeInTheDocument();
  });
});

describe('ProposalCard variants', () => {
  it('test_proposal_card_given_rejected_verdict_expect_rejected_label_and_no_actions', () => {
    render(
      <ProposalCard
        proposal={proposalFixture({
          proposalId: 'p1',
          before: 'a',
          after: 'b',
          note: 'n',
          allowedDecisions: ['accept', 'reject'],
        })}
        verdict="rejected"
      />,
    );

    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText('Rejected')).toBeInTheDocument();
  });

  it('test_proposal_card_given_pending_without_handlers_expect_no_inert_actions', () => {
    render(
      <ProposalCard
        proposal={proposalFixture({
          proposalId: 'p1',
          before: 'a',
          after: 'b',
          note: 'n',
          allowedDecisions: ['accept', 'reject'],
        })}
      />,
    );

    expect(screen.queryByRole('button')).toBeNull();
  });
});

describe('KeepExerciseDialog', () => {
  it('test_keep_exercise_dialog_given_optional_comment_expect_default_owner_reason_saved', async () => {
    const onKeep = vi.fn();
    render(<KeepExerciseDialog exerciseLabel="ex1 · choose" onKeep={onKeep} />);

    await userEvent.click(screen.getByRole('button', { name: 'Keep as is' }));
    const dialog = screen.getByRole('dialog', { name: 'Keep exercise as is' });
    expect(within(dialog).getByLabelText('Comment (optional)')).toHaveValue('');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Keep as is' }));

    expect(onKeep).toHaveBeenCalledWith({ reason: 'still_matches', comment: '' });
  });
});

describe('PaneHeader', () => {
  it('test_pane_header_given_level_one_expect_level_one_heading', () => {
    render(<PaneHeader title="Chat" level={1} />);

    expect(screen.getByRole('heading', { level: 1, name: 'Chat' })).toBeInTheDocument();
  });

  it('test_pane_header_given_default_level_expect_level_two_heading', () => {
    render(<PaneHeader title="Lessons" />);

    expect(screen.getByRole('heading', { level: 2, name: 'Lessons' })).toBeInTheDocument();
  });
});

describe('ContextChip', () => {
  it('test_context_chip_remove_given_keyboard_enter_expect_handler_called', async () => {
    const onRemove = vi.fn();
    render(<ContextChip kind="file" label="lesson.md" onRemove={onRemove} />);

    await userEvent.tab();
    expect(screen.getByRole('button', { name: 'Remove file lesson.md' })).toHaveFocus();
    await userEvent.keyboard('{Enter}');

    expect(onRemove).toHaveBeenCalledOnce();
  });

  it('test_context_chip_given_remove_handler_expect_labelled_remove_button_calling_it', async () => {
    const onRemove = vi.fn();
    render(<ContextChip kind="finding" label="ambiguous-gloss" detail="Jeg lager kaffe nå." onRemove={onRemove} />);

    await userEvent.click(screen.getByRole('button', { name: 'Remove finding ambiguous-gloss' }));

    expect(onRemove).toHaveBeenCalledOnce();
    expect(screen.getByText('Jeg lager kaffe nå.')).toHaveAttribute('lang', 'nb');
  });
});

function SegmentedControlHarness() {
  const [value, setValue] = useState<'read' | 'review'>('review');
  return (
    <SegmentedControl
      label="Mode"
      value={value}
      onValueChange={setValue}
      options={[
        { value: 'read', label: 'Read' },
        { value: 'review', label: 'Review' },
      ]}
    />
  );
}
describe('SegmentedControl', () => {
  it('test_segmented_control_keyboard_given_focus_expect_arrows_move_and_space_selects', async () => {
    render(<SegmentedControlHarness />);
    const review = screen.getByRole('radio', { name: 'Review' });
    const read = screen.getByRole('radio', { name: 'Read' });
    expect(review).toBeChecked();

    await userEvent.tab();
    await userEvent.keyboard('{ArrowLeft}');
    expect(read).toHaveFocus();
    await userEvent.keyboard(' ');

    expect(read).toBeChecked();
    expect(review).not.toBeChecked();
  });

  it('test_segmented_control_given_keyboard_focus_expect_named_radio_remains_selected', async () => {
    render(<SegmentedControlHarness />);

    await userEvent.tab();

    const review = screen.getByRole('radio', { name: 'Review' });
    expect(review).toHaveFocus();
    expect(review).toBeChecked();
  });

  it('test_segmented_control_given_click_on_selected_option_expect_selection_kept', async () => {
    render(<SegmentedControlHarness />);

    await userEvent.click(screen.getByRole('radio', { name: 'Review' }));

    expect(screen.getByRole('radio', { name: 'Review' })).toBeChecked();
  });
});

describe('StatusChip', () => {
  it('test_status_chip_given_tone_expect_tone_attribute_and_label', () => {
    render(<StatusChip tone="error">check error</StatusChip>);

    expect(screen.getByText('check error')).toHaveAttribute('data-tone', 'error');
  });
});
