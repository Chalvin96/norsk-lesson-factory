import type { Meta, StoryObj } from '@storybook/react-vite';

import { ChatPane } from '@/chat/chat-pane';

const meta = {
  title: 'Workbench/ChatPane',
  component: ChatPane,
  parameters: { layout: 'fullscreen' },
  args: { turns: [], chips: [], onSend: () => undefined, onAddChip: () => undefined, onRemoveChip: () => undefined },
} satisfies Meta<typeof ChatPane>;
export default meta;
type Story = StoryObj<typeof meta>;

export const Ready: Story = {};
export const Streaming: Story = {
  args: { pendingSince: Date.now(), partialText: 'Make the first dialogue line shorter for A1.' },
};
export const RestoredActiveTurn: Story = {
  args: {
    turns: [
      {
        role: 'author',
        text: 'Please review the current draft.',
        chips: [],
        toolNote: null,
        turnId: 'restored-turn',
        status: 'running',
        partial: 'The opening is clear so far.',
      },
    ],
    pendingSince: Date.now(),
    partialText: 'The opening is clear so far.',
    onCancel: () => undefined,
  },
};
export const Failed: Story = {
  args: {
    turns: [
      {
        role: 'author',
        text: 'Shorten the opening line.',
        chips: [],
        toolNote: null,
        turnId: 'turn-1',
        status: 'failed',
        error: 'interrupted',
      },
    ],
    onRetryTurn: () => undefined,
  },
};
