import type { Meta, StoryObj } from '@storybook/react-vite';

import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Kbd } from '@/components/ui/kbd';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

const meta = { title: 'UI/Overlays', component: Popover } satisfies Meta<typeof Popover>;
export default meta;
type Story = StoryObj<typeof meta>;

export const TooltipWithShortcut: Story = {
  render: () => (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button variant="outline">Add to chat</Button>
      </TooltipTrigger>
      <TooltipContent>
        Reference the selection <Kbd>Ctrl</Kbd>+<Kbd>L</Kbd>
      </TooltipContent>
    </Tooltip>
  ),
};
export const PopoverPanel: Story = {
  render: () => (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="outline">Approve blocked</Button>
      </PopoverTrigger>
      <PopoverContent className="text-sm">lesson: open proposal · exercises: 1 check error</PopoverContent>
    </Popover>
  ),
};
export const AddContextMenu: Story = {
  render: () => (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline">@ Add context</Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent>
        <DropdownMenuLabel>Files</DropdownMenuLabel>
        <DropdownMenuItem>lesson.md</DropdownMenuItem>
        <DropdownMenuItem>plan.md</DropdownMenuItem>
        <DropdownMenuItem>exercises.yaml</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem>Current section</DropdownMenuItem>
        <DropdownMenuItem>Open findings</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive">Clear context</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  ),
};
