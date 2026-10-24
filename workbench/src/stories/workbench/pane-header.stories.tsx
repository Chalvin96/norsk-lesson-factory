import type { Meta, StoryObj } from '@storybook/react-vite';

import { PaneHeader, StatusChip } from '@/components/workbench';

const meta = { title: 'Workbench/PaneHeader', component: PaneHeader, args: { title: 'Chat' } } satisfies Meta<
  typeof PaneHeader
>;
export default meta;
type Story = StoryObj<typeof meta>;

export const WithChips: Story = {
  render: () => (
    <div className="w-96 rounded-md border bg-white">
      <PaneHeader title="Chat" titleId="chat-title">
        <StatusChip>Codex</StatusChip>
        <StatusChip>AI</StatusChip>
      </PaneHeader>
    </div>
  ),
};
