import type { Meta, StoryObj } from '@storybook/react-vite';
import { fn } from 'storybook/test';

import { ContextChip } from '@/components/workbench';

const meta = {
  title: 'Workbench/ContextChip',
  component: ContextChip,
  args: { kind: 'selection', label: 'A Saturday morning at home', detail: 'Fint. Jeg jobber hjemme i dag.' },
} satisfies Meta<typeof ContextChip>;
export default meta;
type Story = StoryObj<typeof meta>;

export const Kinds: Story = {
  render: () => (
    <div className="flex max-w-md flex-wrap gap-2">
      <ContextChip kind="selection" label="A Saturday morning at home" detail="Fint. Jeg jobber hjemme i dag." />
      <ContextChip kind="finding" label="ambiguous-gloss" detail="Jeg lager kaffe nå." />
      <ContextChip kind="file" label="plan.md" />
    </div>
  ),
};
export const Removable: Story = { args: { onRemove: fn() } };
