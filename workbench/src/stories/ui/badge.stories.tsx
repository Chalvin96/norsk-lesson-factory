import type { Meta, StoryObj } from '@storybook/react-vite';

import { Badge } from '@/components/ui/badge';

const meta = { title: 'UI/Badge', component: Badge, args: { children: 'A1' } } satisfies Meta<typeof Badge>;
export default meta;
type Story = StoryObj<typeof meta>;

export const Variants: Story = {
  render: () => (
    <div className="flex gap-2">
      <Badge>Default</Badge>
      <Badge variant="secondary">Secondary</Badge>
      <Badge variant="destructive">Destructive</Badge>
      <Badge variant="outline">Outline</Badge>
    </div>
  ),
};
