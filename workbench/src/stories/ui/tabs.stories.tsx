import type { Meta, StoryObj } from '@storybook/react-vite';

import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';

const meta = { title: 'UI/Tabs', component: Tabs } satisfies Meta<typeof Tabs>;
export default meta;
type Story = StoryObj<typeof meta>;

export const FormSource: Story = {
  render: () => (
    <Tabs defaultValue="form" className="max-w-md">
      <TabsList>
        <TabsTrigger value="form">Form</TabsTrigger>
        <TabsTrigger value="source">Source</TabsTrigger>
      </TabsList>
      <TabsContent value="form" className="text-sm">
        Per-type block fields.
      </TabsContent>
      <TabsContent value="source" className="font-mono text-xs">
        ::: {'{'}.reading speaker_name="Jonas"{'}'}
      </TabsContent>
    </Tabs>
  ),
};
