import type { Meta, StoryObj } from '@storybook/react-vite';
import { fn } from 'storybook/test';

import { ExerciseAlignmentNotice } from '@/lesson/lifecycle/exercise-alignment-notice';

const meta = {
  title: 'Workbench/Exercise alignment',
  component: ExerciseAlignmentNotice,
  args: {
    exerciseKey: 'notice_name_question',
    exerciseLabel: 'What does Mina ask with «Hva heter du?» · choose',
    onKeep: fn(),
  },
  decorators: [(Story) => <div className="max-w-xl">{Story()}</div>],
} satisfies Meta<typeof ExerciseAlignmentNotice>;
export default meta;
type Story = StoryObj<typeof meta>;

export const Stale: Story = {
  args: {
    alignment: {
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
    },
  },
};

export const Untracked: Story = {
  args: { alignment: { status: 'untracked', exerciseKey: 'match_line_to_job', alignment: null, history: [] } },
};

export const Kept: Story = {
  args: {
    alignment: {
      status: 'aligned',
      exerciseKey: 'notice_name_question',
      alignment: {
        exerciseKey: 'notice_name_question',
        checkedAgainst: { 'a-first-meeting': 'new-hash' },
        sectionSources: { 'a-first-meeting': 'New text' },
        outcome: 'kept',
        reason: null,
        comment: null,
        note: 'The question still asks for the same fact.',
        at: '2026-10-13T12:00:00+00:00',
        decisionId: 'run:notice_name_question',
      },
      history: [
        {
          exerciseKey: 'notice_name_question',
          checkedAgainst: { 'a-first-meeting': 'new-hash' },
          sectionSources: { 'a-first-meeting': 'New text' },
          outcome: 'kept',
          reason: null,
          comment: null,
          note: 'The question still asks for the same fact.',
          at: '2026-10-13T12:00:00+00:00',
          decisionId: 'run:notice_name_question',
        },
      ],
    },
  },
};
