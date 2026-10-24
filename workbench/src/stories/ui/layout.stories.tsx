import type { Meta, StoryObj } from '@storybook/react-vite';

import { ScrollArea } from '@/components/ui/scroll-area';
import { Separator } from '@/components/ui/separator';
import { Skeleton } from '@/components/ui/skeleton';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table';
import { Toggle } from '@/components/ui/toggle';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';

const meta = { title: 'UI/Layout', component: ScrollArea } satisfies Meta<typeof ScrollArea>;
export default meta;
type Story = StoryObj<typeof meta>;

export const ScrollingList: Story = {
  render: () => (
    <ScrollArea className="h-48 w-72 rounded-md border">
      <ol className="p-3 text-sm">
        {Array.from({ length: 20 }, (_, index) => (
          <li key={index} className="py-1">
            {String(index + 1).padStart(2, '0')} · Lesson {index + 1}
          </li>
        ))}
      </ol>
    </ScrollArea>
  ),
};
export const SeparatorAndSkeleton: Story = {
  render: () => (
    <div className="grid max-w-sm gap-3">
      <Skeleton className="h-6 w-2/3" />
      <Separator />
      <Skeleton className="h-4 w-full" />
      <Skeleton className="h-4 w-5/6" />
    </div>
  ),
};
export const WordTable: Story = {
  render: () => (
    <Table className="max-w-md">
      <TableHeader>
        <TableRow>
          <TableHead>Infinitive</TableHead>
          <TableHead>Present</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        <TableRow>
          <TableCell lang="nb">å jobbe</TableCell>
          <TableCell lang="nb">jobber</TableCell>
        </TableRow>
        <TableRow>
          <TableCell lang="nb">å lage</TableCell>
          <TableCell lang="nb">lager</TableCell>
        </TableRow>
      </TableBody>
    </Table>
  ),
};
export const FormatToggles: Story = {
  render: () => (
    <ToggleGroup type="multiple" variant="outline" aria-label="Formatting">
      <ToggleGroupItem value="bold" aria-label="Bold">
        <b>B</b>
      </ToggleGroupItem>
      <ToggleGroupItem value="italic" aria-label="Italic">
        <i>I</i>
      </ToggleGroupItem>
      <ToggleGroupItem value="nb" aria-label="nb, mark as Norwegian">
        nb
      </ToggleGroupItem>
    </ToggleGroup>
  ),
};
export const ToggleVariants: Story = {
  render: () => (
    <div className="grid gap-4">
      <div className="flex items-center gap-2">
        <Toggle aria-label="Pin finding">Pin</Toggle>
        <Toggle variant="outline" aria-label="Show source">
          Source
        </Toggle>
        <Toggle size="sm" variant="outline" defaultPressed aria-label="Small pressed">
          Small
        </Toggle>
        <Toggle size="lg" variant="outline" aria-label="Large">
          Large
        </Toggle>
      </div>
      {(['sm', 'default', 'lg'] as const).map((size) => (
        <ToggleGroup key={size} type="single" size={size} defaultValue="review" aria-label={`Mode, ${size} size`}>
          <ToggleGroupItem value="read">Read</ToggleGroupItem>
          <ToggleGroupItem value="review">Review</ToggleGroupItem>
        </ToggleGroup>
      ))}
    </div>
  ),
};
