import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { ChatPane } from './chat-pane';

describe('ChatPane lifecycle', () => {
  it('test_chat_pane_given_ready_state_expect_codex_backend_badge', () => {
    render(<ChatPane turns={[]} chips={[]} onSend={vi.fn()} onAddChip={vi.fn()} onRemoveChip={vi.fn()} />);

    expect(screen.getByText('Codex')).toBeVisible();
  });

  it('test_chat_streaming_given_partial_reply_expect_provisional_text_and_cancel_action', async () => {
    const onCancel = vi.fn();
    render(
      <ChatPane
        turns={[]}
        chips={[]}
        onSend={vi.fn()}
        onAddChip={vi.fn()}
        onRemoveChip={vi.fn()}
        pendingSince={Date.now()}
        partialText="Make the line shorter"
        onCancel={onCancel}
      />,
    );

    expect(screen.getByRole('status', { name: 'Codex response in progress' })).toHaveTextContent(
      'Make the line shorter',
    );
    expect(screen.getByRole('status', { name: 'Codex response in progress' })).toHaveTextContent(
      'provisional response',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onCancel).toHaveBeenCalledOnce();
  });

  it('test_failed_chat_turn_given_retry_action_expect_same_turn_id_retried', async () => {
    const onRetryTurn = vi.fn();
    const onNewChat = vi.fn();
    render(
      <ChatPane
        turns={[
          {
            role: 'author',
            text: 'Shorten the first line',
            chips: [],
            toolNote: null,
            turnId: 'turn-1',
            status: 'failed',
            error: 'interrupted',
          },
        ]}
        chips={[]}
        onSend={vi.fn()}
        onAddChip={vi.fn()}
        onRemoveChip={vi.fn()}
        onRetryTurn={onRetryTurn}
        onNewChat={onNewChat}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await userEvent.click(screen.getByRole('button', { name: 'New chat' }));

    expect(onRetryTurn).toHaveBeenCalledWith('turn-1');
    expect(onNewChat).toHaveBeenCalledOnce();
  });

  it('test_failed_chat_turn_given_workbench_job_error_expect_friendly_message_and_raw_details_disclosure', async () => {
    const rawError = 'CodexAppServerError: app-server request failed';
    render(
      <ChatPane
        turns={[
          {
            role: 'author',
            text: 'Shorten the first line',
            chips: [],
            toolNote: null,
            turnId: 'turn-1',
            status: 'failed',
            error: rawError,
          },
        ]}
        chips={[]}
        onSend={vi.fn()}
        onAddChip={vi.fn()}
        onRemoveChip={vi.fn()}
        error="The Codex call failed."
        errorDetail={rawError}
      />,
    );

    const alert = screen.getByRole('alert');
    expect(alert).toHaveTextContent('The Codex call failed.');
    const details = alert.querySelector('details');
    expect(details).not.toBeNull();
    await userEvent.click(screen.getByText('Details'));
    expect(details).toHaveAttribute('open');
    expect(alert).toHaveTextContent(rawError);
  });

  it('test_failed_chat_turns_given_newer_job_error_expect_details_only_on_latest_turn', () => {
    const latestRawError = 'BackendDownException: backend timed out after 8s';
    render(
      <ChatPane
        turns={[
          {
            role: 'author',
            text: 'Older request',
            chips: [],
            toolNote: null,
            turnId: 'turn-older',
            status: 'failed',
            error: 'The older request was interrupted.',
          },
          {
            role: 'author',
            text: 'Newer request',
            chips: [],
            toolNote: null,
            turnId: 'turn-newer',
            status: 'failed',
            error: 'The newer request was interrupted.',
          },
        ]}
        chips={[]}
        onSend={vi.fn()}
        onAddChip={vi.fn()}
        onRemoveChip={vi.fn()}
        error="The newer OpenCode Go call timed out."
        errorDetail={latestRawError}
      />,
    );

    const [olderAlert, latestAlert] = screen.getAllByRole('alert');
    expect(olderAlert).toHaveTextContent('The older request was interrupted.');
    expect(olderAlert).not.toHaveTextContent('The newer OpenCode Go call timed out.');
    expect(olderAlert.querySelector('details')).toBeNull();
    expect(latestAlert).toHaveTextContent('The newer OpenCode Go call timed out.');
    expect(latestAlert).toHaveTextContent(latestRawError);
    expect(latestAlert.querySelector('details')).not.toBeNull();
  });

  it('test_chat_shortcut_given_control_slash_expect_input_focused', () => {
    render(<ChatPane turns={[]} chips={[]} onSend={vi.fn()} onAddChip={vi.fn()} onRemoveChip={vi.fn()} />);

    fireEvent.keyDown(window, { key: '/', ctrlKey: true });

    expect(screen.getByRole('textbox', { name: 'Message to chat' })).toHaveFocus();
  });
});
