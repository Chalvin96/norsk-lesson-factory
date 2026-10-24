import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { Button } from './button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from './dialog';
import { Input } from './input';
import { Label } from './label';
import { Select, SelectTrigger } from './select';
import { Textarea } from './textarea';

describe('Contrast scope', () => {
  it.each(['default', 'outline', 'secondary', 'ghost', 'link', 'destructive', 'dialogSecondary'] as const)(
    'test_button_given_disabled_%s_expect_pointer_events_none',
    (variant) => {
      render(
        <Button variant={variant} disabled>
          Action
        </Button>,
      );
      expect(screen.getByRole('button')).toHaveClass('disabled:pointer-events-none');
    },
  );
  it('test_buttons_given_toolbar_variants_expect_original_distinct_styles', () => {
    render(
      <>
        <Button variant="outline">Outline</Button>
        <Button variant="secondary">Secondary</Button>
      </>,
    );
    expect(screen.getByText('Outline')).toHaveClass('bg-background', 'border-input');
    expect(screen.getByText('Secondary')).toHaveClass('bg-secondary', 'text-secondary-foreground');
  });
  it('test_button_given_dialog_secondary_expect_opaque_strong_border', () => {
    render(<Button variant="dialogSecondary">Cancel</Button>);
    expect(screen.getByRole('button')).toHaveClass('border-border-strong', 'bg-popover', 'text-popover-foreground');
  });
  it('test_dialog_given_open_expect_scoped_primary_and_matching_close_offset', () => {
    render(
      <Dialog open>
        <DialogContent aria-describedby={undefined}>
          <DialogTitle>Title</DialogTitle>
          <DialogDescription>Context</DialogDescription>
        </DialogContent>
      </Dialog>,
    );
    expect(screen.getByText('Context')).toHaveClass('text-muted-foreground');
    expect(screen.getByRole('dialog')).toHaveClass('bg-popover', '[--primary:var(--button-primary)]');
    expect(screen.getByRole('button', { name: 'Close' })).toHaveClass('ring-offset-popover');
  });
  it.each([
    ['input', <Input key="input" aria-label="field" />],
    ['textarea', <Textarea key="textarea" aria-label="field" />],
    [
      'select',
      <Select key="select">
        <SelectTrigger aria-label="field" />
      </Select>,
    ],
  ])('test_field_given_%s_expect_strong_border_and_stable_focus_width', (_name, field) => {
    render(<div className="dialog-content">{field}</div>);
    const control = screen.getByLabelText('field');
    expect(control).toHaveClass('in-[.dialog-content]:border-border-strong', 'in-[.dialog-content]:bg-popover');
    expect(control.className).toContain('focus-visible:ring-');
    expect(control).toHaveClass('aria-invalid:border-destructive');
    expect(control).not.toHaveClass('focus-visible:border-2');
  });
  it('test_label_given_form_expect_tested_foreground', () => {
    render(<Label htmlFor="title">Title</Label>);
    expect(screen.getByText('Title')).toHaveClass('text-foreground');
  });
  it('test_button_given_disabled_primary_in_dialog_expect_tested_readable_colors', () => {
    render(
      <div className="dialog-content">
        <Button disabled>Save</Button>
      </div>,
    );
    expect(screen.getByRole('button')).toHaveClass(
      'in-[.dialog-content]:disabled:bg-control-disabled',
      'in-[.dialog-content]:disabled:border-border',
      'in-[.dialog-content]:disabled:text-muted-foreground',
      'in-[.dialog-content]:disabled:opacity-100',
    );
  });
  it('test_dialog_layout_given_shared_header_and_footer_expect_responsive_defaults', () => {
    const { container } = render(
      <>
        <DialogHeader>Heading</DialogHeader>
        <DialogFooter>Actions</DialogFooter>
      </>,
    );
    expect(container.firstChild).toHaveClass('text-center', 'sm:text-left');
    expect(container.lastChild).not.toHaveClass('[&>button]:w-full');
  });
});
