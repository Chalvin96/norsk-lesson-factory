import type { Meta, StoryObj } from '@storybook/react-vite';

import { StatusChip } from '@/components/workbench';

const meta = { title: 'Workbench/StatusChip', component: StatusChip, args: { children: 'baseline' } } satisfies Meta<
  typeof StatusChip
>;
export default meta;
type Story = StoryObj<typeof meta>;

export const Tones: Story = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      <StatusChip tone="neutral">baseline</StatusChip>
      <StatusChip tone="draft">edited since approval</StatusChip>
      <StatusChip tone="warning">findings 3</StatusChip>
      <StatusChip tone="error">check error</StatusChip>
      <StatusChip tone="ok">owner accepted</StatusChip>
    </div>
  ),
};
