import type { Meta, StoryObj } from '@storybook/react-vite';
import { PlusIcon } from 'lucide-react';

import { Button } from '@/components/ui/button';

const meta = { title: 'UI/Button', component: Button, args: { children: 'Run AI review' } } satisfies Meta<
  typeof Button
>;
export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {};
export const Variants: Story = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      <Button>Approve</Button>
      <Button variant="secondary">Secondary</Button>
      <Button variant="outline">Regenerate…</Button>
      <Button variant="ghost">Settings</Button>
      <Button variant="destructive">Discard draft</Button>
      <Button variant="link">Open lesson</Button>
    </div>
  ),
};
export const Sizes: Story = {
  render: () => (
    <div className="flex items-center gap-2">
      <Button size="sm">Small</Button>
      <Button>Default</Button>
      <Button size="lg">Large</Button>
      <Button size="icon" aria-label="Add block">
        <PlusIcon />
      </Button>
    </div>
  ),
};
export const Disabled: Story = { args: { disabled: true, children: 'Save' } };
