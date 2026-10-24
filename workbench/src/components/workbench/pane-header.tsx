import type * as React from 'react';

import { cn } from '@/lib/utils';

export interface PaneHeaderProps {
  title: string;
  titleId?: string;
  level?: 1 | 2;
  children?: React.ReactNode;
  className?: string;
}

export function PaneHeader({ title, titleId, level = 2, children, className }: PaneHeaderProps) {
  const Heading = level === 1 ? 'h1' : 'h2';
  return (
    <header className={cn('flex min-h-11 shrink-0 items-center gap-2 border-b px-4', className)}>
      <Heading
        id={titleId}
        className="m-0 font-display text-xs font-semibold tracking-[0.16em] text-muted-foreground uppercase"
      >
        {title}
      </Heading>
      {children}
    </header>
  );
}
