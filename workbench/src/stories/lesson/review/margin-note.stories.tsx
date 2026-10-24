import type { Meta, StoryObj } from '@storybook/react-vite';
import { fn } from 'storybook/test';
import type { WorkbenchFinding } from '@/api/contracts';
import { MarginNote } from '@/lesson/review/margin-note';
import { findingFixture } from '@/test/wire-fixtures';

const finding: WorkbenchFinding = findingFixture({
  findingId: 'f1',
  target: { elementId: 'intro', blockIndex: 0 },
  origin: 'ai',
  severity: 'warning',
  advisory: true,
  status: 'open',
  code: 'ambiguous-gloss',
  label: 'Natural phrasing',
  excerpt: 'Jeg lager kaffe nå.',
  explanation: 'The English gloss could be read two ways by a beginner.',
  message: 'The wording may suggest a different action than the lesson intends.',
  quoteEnglish: 'I am making coffee now.',
  suggestedChange: 'Use a more specific verb for preparing the coffee.',
  details: '{"criterion":"ambiguous-gloss","reviewer":"primary"}',
});

const meta = {
  title: 'Workbench/MarginNote',
  component: MarginNote,
  args: { finding, onAskChat: fn(), onResolve: fn() },
  decorators: [(Story) => <div className="w-64">{Story()}</div>],
} satisfies Meta<typeof MarginNote>;
export default meta;
type Story = StoryObj<typeof meta>;

export const OpenWarning: Story = {};
export const ReadableSuggestion: Story = {};
export const Advisory: Story = {
  args: {
    finding: { ...finding, advisory: true },
    onFix: fn(),
  },
};
export const OpenCheckError: Story = {
  args: {
    finding: {
      ...finding,
      origin: 'check',
      severity: 'error',
      advisory: false,
      code: 'exercise-stale-reference',
      explanation: 'The exercise refers to wording the lesson no longer contains.',
    },
  },
};
export const Current: Story = { args: { isCurrent: true } };
export const Resolved: Story = { args: { finding: { ...finding, status: 'resolved' } } };
