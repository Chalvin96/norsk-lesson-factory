import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { proposalFixture } from '@/test/wire-fixtures';

import { ProposalCard } from './proposal-card';
import { ProposalDiff } from './proposal-diff';

describe('Rendered proposal comparison', () => {
  it('test_proposal_diff_given_large_changed_paragraph_expect_coarse_line_highlights', () => {
    const sharedWords = Array.from({ length: 450 }, (_, index) => `word${index}`).join(' ');
    const before = `shared removed ${sharedWords}`;
    const after = `shared inserted ${sharedWords}`;
    const { container } = render(
      <ProposalDiff before={[{ text: before, lang: 'en' }]} after={[{ text: after, lang: 'en' }]} />,
    );

    const removedWords = Array.from(container.querySelectorAll('del'), (element) => element.textContent).join(' ');
    const insertedWords = Array.from(container.querySelectorAll('ins'), (element) => element.textContent).join(' ');
    expect(removedWords).toMatch(/^shared removed word0/);
    expect(insertedWords).toMatch(/^shared inserted word0/);
  });

  it('test_proposal_given_markdown_source_expect_rendered_words_changed_word_highlights_and_raw_disclosure', async () => {
    const before = '::: {.reading translation="Hello"}\nJeg lager kaffe.\n:::';
    const after = '::: {.reading translation="Hello"}\nJeg lager te.\n:::';
    const { container } = render(
      <ProposalCard
        proposal={proposalFixture({
          proposalId: 'p1',
          before,
          after,
          note: 'Change drink',
          allowedDecisions: ['accept', 'reject'],
          beforeRendered: [{ text: 'Jeg lager kaffe.', lang: 'nb' }],
          afterRendered: [{ text: 'Jeg lager te.', lang: 'nb' }],
        })}
      />,
    );
    const oldText = screen.getByLabelText('Rendered before');
    const newText = screen.getByLabelText('Rendered after');
    expect(oldText).toHaveTextContent('Jeg lager kaffe.');
    expect(oldText).not.toHaveTextContent(':::');
    expect(newText).not.toHaveTextContent('translation=');
    expect(container.querySelector('del')).toHaveTextContent('kaffe.');
    expect(container.querySelector('ins')).toHaveTextContent('te.');
    expect(oldText.querySelector('p')).toHaveAttribute('lang', 'nb');
    expect(screen.getByLabelText('Before source')).not.toBeVisible();
    await userEvent.click(screen.getByText('Raw source'));
    expect(screen.getByLabelText('Before source')).toBeVisible();
    expect(screen.getByLabelText('After source').textContent).toBe(after);
  });

  it('test_proposal_given_hostile_text_expect_escaped_rendered_comparison_and_source', () => {
    const hostile = '<img src=x onerror=alert(1)>';
    const { container } = render(
      <ProposalCard
        proposal={proposalFixture({
          proposalId: 'p1',
          before: hostile,
          after: hostile,
          note: 'Inspect',
          allowedDecisions: ['accept', 'reject'],
          beforeRendered: [{ text: hostile, lang: 'en' }],
          afterRendered: [{ text: hostile, lang: 'en' }],
        })}
      />,
    );
    expect(screen.getByLabelText('Rendered before')).toHaveTextContent(hostile);
    expect(container.querySelector('img')).toBeNull();
    expect(within(screen.getByRole('region', { name: 'AI proposal' })).getByText('Raw source')).toBeInTheDocument();
  });
});
