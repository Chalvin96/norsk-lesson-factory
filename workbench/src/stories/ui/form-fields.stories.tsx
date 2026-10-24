import type { Meta, StoryObj } from '@storybook/react-vite';

import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';

const meta = { title: 'UI/Form fields', component: Input } satisfies Meta<typeof Input>;
export default meta;
type Story = StoryObj<typeof meta>;

export const DialogueLineForm: Story = {
  render: () => (
    <div className="grid max-w-md gap-4">
      <div className="grid gap-1.5">
        <Label htmlFor="speaker">Speaker</Label>
        <Input id="speaker" defaultValue="Jonas" />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="norwegian">Norwegian</Label>
        <Input id="norwegian" lang="nb" defaultValue="Jeg lager kaffe nå." />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="translation">Translation</Label>
        <Textarea id="translation" defaultValue="I'm making coffee now." />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="scope">Review scope</Label>
        <Select defaultValue="lesson">
          <SelectTrigger id="scope" className="w-48">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="lesson">Whole lesson</SelectItem>
            <SelectItem value="section">Current section</SelectItem>
            <SelectItem value="item">Selected item</SelectItem>
          </SelectContent>
        </Select>
      </div>
    </div>
  ),
};
export const Disabled: Story = { args: { disabled: true, defaultValue: 'Read-only', 'aria-label': 'Disabled field' } };
export const SelectSizes: Story = {
  render: () => (
    <div className="flex items-center gap-3">
      {(['sm', 'default'] as const).map((size) => (
        <Select key={size} defaultValue="lesson">
          <SelectTrigger size={size} aria-label={`Review scope, ${size} size`} className="w-40">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="lesson">Whole lesson</SelectItem>
            <SelectItem value="section">Current section</SelectItem>
          </SelectContent>
        </Select>
      ))}
    </div>
  ),
};
