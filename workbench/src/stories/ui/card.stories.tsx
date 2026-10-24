import type { Meta, StoryObj } from '@storybook/react-vite';

import { Button } from '@/components/ui/button';
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card';

const meta = { title: 'UI/Card', component: Card } satisfies Meta<typeof Card>;
export default meta;
type Story = StoryObj<typeof meta>;

export const Default: Story = {
  render: () => (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle>Present Tense for Now and Habits</CardTitle>
        <CardDescription>A1 · Grammar · canonical</CardDescription>
      </CardHeader>
      <CardContent className="text-sm">Form and understand simple Bokmål present-tense statements.</CardContent>
      <CardFooter className="gap-2">
        <Button>Open lesson</Button>
        <Button variant="outline">Review dependencies</Button>
      </CardFooter>
    </Card>
  ),
};
