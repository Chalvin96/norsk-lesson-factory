import type { Meta, StoryObj } from '@storybook/react-vite';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { Toaster } from '@/components/ui/sonner';

const meta = { title: 'UI/Toaster', component: Toaster } satisfies Meta<typeof Toaster>;
export default meta;
type Story = StoryObj<typeof meta>;

export const SaveResult: Story = {
  render: () => (
    <>
      <Button onClick={() => toast('Saved · one commit on authoring')}>Save</Button>
      <Toaster />
    </>
  ),
};
