import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { lessonPayload, renderAt, rowElement } from '../test/fixtures';
import { navigate } from './router';

describe('unsaved work navigation guard', () => {
  it('test_dirty_block_given_curriculum_link_clicked_expect_stay_or_discard_before_navigation', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    await userEvent.click(within(rowElement('intro-b0')).getByRole('button', { name: 'Edit block' }));
    await userEvent.type(screen.getByLabelText('Translation'), ' Keep this.');

    await userEvent.click(screen.getByRole('link', { name: 'Curriculum' }));

    const dialog = await screen.findByRole('dialog', { name: 'Leave with unsaved edits?' });
    expect(window.location.pathname).toBe('/lessons/demo');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Stay here' }));
    expect(screen.getByLabelText('Translation')).toHaveValue("I'm making coffee. Keep this.");

    await userEvent.click(screen.getByRole('link', { name: 'Curriculum' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Discard and continue' }));

    await waitFor(() => expect(window.location.pathname).toBe('/'));
    expect(screen.getByRole('button', { name: 'Save curriculum' })).toBeInTheDocument();
  });

  it('test_dirty_block_given_browser_history_back_expect_restore_then_confirm_navigation', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    act(() => {
      navigate('/settings');
      navigate('/lessons/demo');
    });
    await userEvent.click(within(rowElement('intro-b0')).getByRole('button', { name: 'Edit block' }));
    await userEvent.type(screen.getByLabelText('Translation'), ' Keep this.');

    await act(async () => {
      window.history.back();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(await screen.findByRole('dialog', { name: 'Leave with unsaved edits?' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/lessons/demo');
    await userEvent.click(screen.getByRole('button', { name: 'Discard and continue' }));

    await waitFor(() => expect(window.location.pathname).toBe('/settings'));
  });

  it('test_dirty_block_given_history_discard_within_lesson_expect_editor_cleared_and_next_navigation_unblocked', async () => {
    const lesson = lessonPayload({
      draft: { exists: true, startedFrom: 'synthetic', dirty: true, lastCommit: null },
    });
    renderAt('/lessons/demo?stage=plan', { lesson });
    act(() => navigate('/lessons/demo'));
    await userEvent.click(within(rowElement('intro-b0')).getByRole('button', { name: 'Edit block' }));
    await userEvent.type(screen.getByLabelText('Translation'), ' Discard this.');

    await act(async () => {
      window.history.back();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    expect(await screen.findByRole('dialog', { name: 'Leave with unsaved edits?' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Discard and continue' }));
    await waitFor(() => expect(window.location.search).toBe('?stage=plan'));

    expect(screen.queryByLabelText('Translation')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
    await userEvent.click(screen.getByRole('link', { name: 'Curriculum' }));
    await waitFor(() => expect(window.location.pathname).toBe('/'));
    expect(screen.queryByRole('dialog', { name: 'Leave with unsaved edits?' })).not.toBeInTheDocument();
  });

  it('test_dirty_block_given_browser_history_forward_expect_restore_then_confirm_navigation', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    act(() => navigate('/settings'));
    await act(async () => {
      window.history.back();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
    await waitFor(() => expect(window.location.pathname).toBe('/lessons/demo'));

    await userEvent.click(within(rowElement('intro-b0')).getByRole('button', { name: 'Edit block' }));
    await userEvent.type(screen.getByLabelText('Translation'), ' Keep this.');
    await act(async () => {
      window.history.forward();
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(await screen.findByRole('dialog', { name: 'Leave with unsaved edits?' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/lessons/demo');
    await userEvent.click(screen.getByRole('button', { name: 'Discard and continue' }));
    await waitFor(() => expect(window.location.pathname).toBe('/settings'));
  });

  it('test_dirty_block_given_lesson_switch_selected_expect_navigation_confirmation', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    await userEvent.click(within(rowElement('intro-b0')).getByRole('button', { name: 'Edit block' }));
    await userEvent.type(screen.getByLabelText('Translation'), ' Keep this.');
    await userEvent.click(screen.getByRole('button', { name: /Switch lesson/ }));
    await userEvent.click(await screen.findByRole('link', { name: /Second lesson/ }));

    const dialog = await screen.findByRole('dialog', { name: 'Leave with unsaved edits?' });
    expect(window.location.pathname).toBe('/lessons/demo');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Stay here' }));
  });

  it('test_dirty_block_given_beforeunload_event_expect_browser_warning_requested', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    await userEvent.click(within(rowElement('intro-b0')).getByRole('button', { name: 'Edit block' }));
    await userEvent.type(screen.getByLabelText('Translation'), ' Keep this.');

    const event = new Event('beforeunload', { cancelable: true });
    fireEvent(window, event);

    expect(event.defaultPrevented).toBe(true);
  });

  it('test_staged_curriculum_order_given_lesson_link_clicked_expect_navigation_confirmation', async () => {
    renderAt('/');
    screen.getByRole('button', { name: 'Open Third lesson details' }).focus();
    await userEvent.keyboard('{Alt>}{ArrowUp}{/Alt}');
    await userEvent.click(screen.getByRole('link', { name: 'Lesson' }));

    const dialog = await screen.findByRole('dialog', { name: 'Leave with unsaved edits?' });
    expect(window.location.pathname).toBe('/');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Stay here' }));
  });
});
