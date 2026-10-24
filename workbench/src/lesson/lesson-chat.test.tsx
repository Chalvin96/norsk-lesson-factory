import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { lessonPayload, renderAt } from '@/test/fixtures';
import { chatTurnFixture } from '@/test/wire-fixtures';

const lessonDocumentRenderCount = vi.hoisted(() => ({ blockView: 0 }));

vi.mock('@/lesson/document/render/blocks', async (importOriginal) => {
  const blocks = await importOriginal<typeof import('@/lesson/document/render/blocks')>();
  const React = await import('react');
  return {
    ...blocks,
    BlockView: (props: Parameters<typeof blocks.BlockView>[0]) => {
      lessonDocumentRenderCount.blockView += 1;
      return React.createElement(blocks.BlockView, props);
    },
  };
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.removeItem('regeneration-demo');
});

describe('Lesson chat', () => {
  it('test_persisted_lesson_chat_given_page_reload_expect_monitoring_cancel_and_terminal_refresh', async () => {
    const lesson = lessonPayload();
    lesson.state.chat = [
      chatTurnFixture({
        role: 'author',
        text: 'Explain this lesson.',
        turnId: 'restored-lesson-turn',
        status: 'running',
        partial: 'Provisional lesson response.',
      }),
    ];
    const cancelled = {
      ...lesson,
      state: {
        ...lesson.state,
        chat: lesson.state.chat.map((turn) => ({ ...turn, status: 'cancelled' as const, partial: '' })),
      },
    };
    let lessonReads = 0;
    const fetch = vi.fn(async (path: string) => {
      if (path === '/api/lessons/demo') {
        lessonReads += 1;
        return new Response(JSON.stringify(lessonReads === 1 ? lesson : cancelled), {
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify({}), { headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });

    expect(screen.getByRole('status', { name: 'Codex response in progress' })).toHaveTextContent(
      'Provisional lesson response.',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(fetch).toHaveBeenCalledWith(
      '/api/lessons/demo/chat/restored-lesson-turn/cancel',
      expect.objectContaining({ method: 'POST' }),
    );
    await waitFor(() => expect(screen.getByText('Cancelled')).toBeInTheDocument(), { timeout: 3000 });
    expect(lessonReads).toBeGreaterThanOrEqual(2);
  });

  it('test_explain_only_turn_given_page_reload_expect_progress_and_cancel_control', async () => {
    const lesson = lessonPayload();
    lesson.state.chat = [];
    lesson.state.activeTurn = { turnId: 'restored-explain-turn', status: 'running', partial: '' };
    let lessonReads = 0;
    const fetch = vi.fn(async (path: string) => {
      if (path === '/api/lessons/demo') {
        lessonReads += 1;
        const current = lessonReads === 1 ? lesson : { ...lesson, state: { ...lesson.state, activeTurn: null } };
        return new Response(JSON.stringify(current), { headers: { 'Content-Type': 'application/json' } });
      }
      return new Response(JSON.stringify({}), { headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson });

    expect(screen.getByRole('status', { name: 'Codex response in progress' })).toHaveTextContent('Working…');
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(fetch).toHaveBeenCalledWith(
      '/api/lessons/demo/chat/restored-explain-turn/cancel',
      expect.objectContaining({ method: 'POST' }),
    );
    await waitFor(() => expect(lessonReads).toBeGreaterThanOrEqual(2), { timeout: 3000 });
  });

  it('test_chat_action_given_toolbar_expect_keyboard_shortcut_visible_without_popup', () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    const addToChat = screen.getByRole('button', { name: 'Add to chat' });
    expect(addToChat).toHaveAttribute('aria-keyshortcuts', 'Control+L');
    expect(within(addToChat).getByText('Ctrl')).toBeInTheDocument();
    expect(within(addToChat).getByText('L')).toBeInTheDocument();
  });

  it('test_lesson_document_given_chat_context_chip_added_expect_rows_do_not_rerender', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    expect(lessonDocumentRenderCount.blockView).toBeGreaterThan(0);
    lessonDocumentRenderCount.blockView = 0;

    await userEvent.click(screen.getByRole('combobox', { name: 'Add context' }));
    await userEvent.click(await screen.findByRole('option', { name: 'Current section' }));

    expect(lessonDocumentRenderCount.blockView).toBe(0);
  });
});
