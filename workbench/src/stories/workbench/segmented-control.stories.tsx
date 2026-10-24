import type { Meta, StoryObj } from '@storybook/react-vite';
import { useState } from 'react';

import { SegmentedControl } from '@/components/workbench';

function ModeSwitch({ size = 'default' }: { size?: 'sm' | 'default' }) {
  const [mode, setMode] = useState<'read' | 'review'>('review');
  return (
    <SegmentedControl
      label="Mode"
      size={size}
      value={mode}
      onValueChange={setMode}
      options={[
        { value: 'read', label: 'Read' },
        { value: 'review', label: 'Review' },
      ]}
    />
  );
}

const meta = { title: 'Workbench/SegmentedControl', component: ModeSwitch } satisfies Meta<typeof ModeSwitch>;
export default meta;
type Story = StoryObj<typeof meta>;

export const ReadReview: Story = {};
export const Small: Story = { args: { size: 'sm' } };
