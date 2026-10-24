import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { lessonPayload, renderAt } from '@/test/fixtures';

describe('lesson URL state', () => {
  it('test_lesson_url_state_given_mode_and_view_navigation_then_history_expect_state_restored', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    await userEvent.click(await screen.findByRole('radio', { name: 'Read' }));
    await waitFor(() => expect(window.location.search).toBe('?mode=read'));
    await userEvent.click(screen.getByRole('radio', { name: 'Source' }));
    await waitFor(() => expect(window.location.search).toBe('?mode=read&view=source'));

    await act(async () => {
      window.history.back();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitFor(() => expect(window.location.search).toBe('?mode=read'));
    expect(screen.getByRole('radio', { name: 'Render' })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: 'Read' })).toHaveAttribute('aria-checked', 'true');

    await act(async () => {
      window.history.forward();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitFor(() => expect(window.location.search).toBe('?mode=read&view=source'));
    expect(screen.getByRole('radio', { name: 'Source' })).toHaveAttribute('aria-checked', 'true');
  });
});
