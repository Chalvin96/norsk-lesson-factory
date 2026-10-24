import type { Meta, StoryObj } from '@storybook/react-vite';
import { fn } from 'storybook/test';

import { ProposalCard } from '@/lesson/review/proposal-card';
import { proposalFixture } from '@/test/wire-fixtures';

const meta = {
  title: 'Workbench/ProposalCard',
  component: ProposalCard,
  args: {
    proposal: proposalFixture({
      proposalId: 'p1',
      before: 'Jeg lager kaffe nå.',
      after: 'Jeg lager kaffe akkurat nå.',
      note: 'Adds “akkurat” to mark the present moment.',
      allowedDecisions: ['accept', 'reject'],
    }),
    onAccept: fn(),
    onReject: fn(),
    onKeepAsIs: fn(),
  },
  decorators: [(Story) => <div className="max-w-xl">{Story()}</div>],
} satisfies Meta<typeof ProposalCard>;
export default meta;
type Story = StoryObj<typeof meta>;

export const Pending: Story = {};
export const Accepted: Story = { args: { verdict: 'accepted' } };
export const Rejected: Story = { args: { verdict: 'rejected' } };
export const RegenerationDecisionSupport: Story = {
  args: {
    proposal: proposalFixture({
      proposalId: 'regen-1',
      origin: 'regenerate',
      allowedDecisions: ['accept', 'reject', 'keep'],
      before: 'Explanation: Hva = what, heter du = are you called.',
      after: 'Explanation: Mina asks «Hva heter du?» on its own line. Hva = what, heter du = are you called.',
      note: 'Stale exercise · a-first-meeting changed · minimal change',
      changeSummary: 'The prompt and options stay unchanged. Only the explanation now quotes the shorter line.',
      changedSections: [
        {
          sectionId: 'a-first-meeting',
          beforeText: 'Hei! Jeg heter Mina. Hva heter du?',
          afterText: 'Hei! Jeg heter Mina.\nHva heter du?',
          summary: 'The question is now on its own line; its wording is unchanged.',
        },
      ],
      translations: [{ norwegian: 'Hva heter du?', english: 'What is your name?' }],
      answerImpact: 'none',
      uncertainty: null,
    }),
    exerciseLabel: 'What does Mina ask with «Hva heter du?» · choose',
  },
};

export const NonExerciseRegeneration: Story = {
  args: {
    proposal: proposalFixture({
      proposalId: 'regen-reading-1',
      origin: 'regenerate',
      allowedDecisions: ['accept', 'reject'],
      before: '::: {.reading}\nJeg lager kaffe nå.\n:::',
      after: '::: {.reading}\nJeg lager te nå.\n:::',
      note: 'Reading block · scoped regeneration',
      changeSummary: 'The reading now uses te instead of kaffe.',
    }),
  },
};

export const ChatDecisionSupport: Story = {
  args: {
    proposal: proposalFixture({
      proposalId: 'chat-1',
      origin: 'chat',
      allowedDecisions: ['accept', 'reject'],
      before: 'translation: "Hi! What is your name?"',
      after: 'translation: "Hi! What\'s your name?"',
      note: 'A warmer English gloss for the greeting.',
      changeSummary: 'Renders the greeting with a warmer, more conversational English gloss.',
      translations: [{ norwegian: 'Hva heter du?', english: "What's your name?" }],
      answerImpact: 'none',
      uncertainty: null,
    }),
  },
};
