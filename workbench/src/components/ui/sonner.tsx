import type * as React from 'react';
import { Toaster as Sonner } from 'sonner';

type ToasterProps = React.ComponentProps<typeof Sonner>;

const toasterStyle = {
  '--normal-bg': 'var(--popover)',
  '--normal-text': 'var(--popover-foreground)',
  '--normal-border': 'var(--border)',
} as React.CSSProperties;

const Toaster = (props: ToasterProps) => (
  <Sonner theme="light" position="top-right" className="toaster group" style={toasterStyle} {...props} />
);

export { Toaster };
