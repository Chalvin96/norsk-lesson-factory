/** Entry point: `PageStatus` renders a simple loading or error state for a workbench page. */

import type * as React from 'react';

export function PageStatus({ children, tone }: { children: React.ReactNode; tone?: 'error' }) {
  return (
    <main id="workbench-main" tabIndex={-1} className="grid flex-1 place-items-center p-8">
      <p
        role={tone === 'error' ? 'alert' : 'status'}
        className={tone === 'error' ? 'text-destructive-80' : 'text-muted-foreground'}
      >
        {children}
      </p>
    </main>
  );
}
