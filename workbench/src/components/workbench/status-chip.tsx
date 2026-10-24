import { cva } from 'class-variance-authority';
import type * as React from 'react';
import type { ChipTone } from '@/api/contracts';
import { cn } from '@/lib/utils';

export const statusChipVariants = cva(
  'inline-flex h-6 shrink-0 items-center gap-1 rounded-full border px-2.5 text-xs font-semibold whitespace-nowrap',
  {
    variants: {
      tone: {
        neutral: 'border-dashed border-border bg-transparent text-muted-foreground',
        draft: 'border-primary-20 bg-primary-10 text-primary-90',
        warning: 'border-warning-20 bg-warning-0 text-warning-80',
        error: 'border-destructive-20 bg-destructive-0 text-destructive-80',
        ok: 'border-accent-20 bg-accent-10 text-accent-90',
      },
    },
    defaultVariants: { tone: 'neutral' },
  },
);

export interface StatusChipProps extends React.ComponentProps<'span'> {
  tone?: ChipTone;
}

export function StatusChip({ tone, className, ...props }: StatusChipProps) {
  return <span data-tone={tone ?? 'neutral'} className={cn(statusChipVariants({ tone }), className)} {...props} />;
}
