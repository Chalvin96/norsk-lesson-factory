import { QueryClient } from '@tanstack/react-query';
import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { CurriculumPayload } from '@/api/contracts';
import { App } from '@/app/app';
import { curriculumPayload, renderAt, workbenchSettingsPayload } from '@/test/fixtures';
import { chatTurnFixture } from '@/test/wire-fixtures';

import { moveItem } from './curriculum-page';

function renderCurriculum(data: CurriculumPayload): QueryClient {
  window.history.pushState(null, '', '/');
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  queryClient.setQueryData(['curriculum'], data);
  queryClient.setQueryData(['settings'], workbenchSettingsPayload());
  render(<App queryClient={queryClient} />);
  return queryClient;
}

describe('moveItem', () => {
  it('test_move_item_given_move_inside_list_expect_relocated_copy_and_unchanged_input', () => {
    const items = ['a', 'b', 'c'];

    expect(moveItem(items, 2, 0)).toEqual(['c', 'a', 'b']);
    expect(items).toEqual(['a', 'b', 'c']);
  });

  it('test_move_item_given_index_outside_list_expect_unchanged_copy', () => {
    expect(moveItem(['a', 'b'], 0, 5)).toEqual(['a', 'b']);
  });
});

describe('Curriculum tab', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('test_curriculum_rows_given_loaded_check_counts_expect_lesson_page_count_chip', async () => {
    const data = curriculumPayload();
    const queryClient = renderCurriculum(data);

    expect(screen.queryByText(/\d+ checks?$/)).not.toBeInTheDocument();
    act(() => queryClient.setQueryData(['curriculum', 'checks'], { counts: { demo: 1, second: 0 } }));

    const demo = await waitFor(() => document.querySelector('[data-entry-id="demo"]') as HTMLElement);
    expect(await within(demo).findByText('1 check')).toBeInTheDocument();
    const second = document.querySelector('[data-entry-id="second"]') as HTMLElement;
    expect(within(second).getByText('0 checks')).toBeInTheDocument();
  });

  it('test_pristine_curriculum_given_server_reorders_entries_expect_new_order_without_unsaved_edits', async () => {
    const data = curriculumPayload();
    const queryClient = renderCurriculum(data);
    const refreshed = { ...data, lessons: [...data.lessons].reverse() };

    act(() => queryClient.setQueryData(['curriculum'], refreshed));

    await waitFor(() =>
      expect(
        Array.from(document.querySelectorAll('[data-entry-id]'), (entry) => entry.getAttribute('data-entry-id')),
      ).toEqual(['third', 'second', 'demo']),
    );
    expect(screen.getByText('Order saved')).toBeInTheDocument();
    expect(screen.queryByText('Unsaved edits')).not.toBeInTheDocument();
  });

  it('test_local_curriculum_order_given_membership_changes_expect_removed_ids_dropped_and_new_ids_appended', async () => {
    const data = curriculumPayload();
    const queryClient = renderCurriculum(data);
    screen.getByRole('button', { name: 'Open Third lesson details' }).focus();
    await userEvent.keyboard('{Alt>}{ArrowUp}{/Alt}');
    const refreshed = {
      ...data,
      lessons: [
        data.lessons[0],
        {
          lessonId: 'fourth',
          title: 'Fourth lesson',
          cefrLevel: 'A1' as const,
          sequenceIndex: 1,
          source: 'missing' as const,
        },
        { ...data.lessons[2], sequenceIndex: 2 },
      ],
    };

    act(() => queryClient.setQueryData(['curriculum'], refreshed));

    await waitFor(() =>
      expect(
        Array.from(document.querySelectorAll('[data-entry-id]'), (entry) => entry.getAttribute('data-entry-id')),
      ).toEqual(['demo', 'third', 'fourth']),
    );
    expect(screen.getByRole('button', { name: 'Open Fourth lesson details' })).toBeInTheDocument();
    expect(screen.getByText('Unsaved edits')).toBeInTheDocument();
  });

  it('test_failed_curriculum_refresh_given_local_reorder_expect_workspace_and_order_preserved', async () => {
    const data = curriculumPayload();
    const curriculumFetch = vi
      .fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({ error: 'temporary read failure' }), { status: 503 }))
      .mockResolvedValue(new Response(JSON.stringify(data), { headers: { 'Content-Type': 'application/json' } }));
    vi.stubGlobal('fetch', (path: string) =>
      path === '/api/curriculum/checks'
        ? Promise.resolve(new Response(JSON.stringify({ counts: {} })))
        : curriculumFetch(path),
    );
    const queryClient = renderCurriculum(data);
    screen.getByRole('button', { name: 'Open Third lesson details' }).focus();
    await userEvent.keyboard('{Alt>}{ArrowUp}{/Alt}');

    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['curriculum'] });
    });

    expect(await screen.findByRole('alert')).toHaveTextContent('temporary read failure');
    expect(
      Array.from(document.querySelectorAll('[data-entry-id]'), (entry) => entry.getAttribute('data-entry-id')),
    ).toEqual(['demo', 'third', 'second']);
    expect(screen.getByText('Unsaved edits')).toBeInTheDocument();

    await act(async () => {
      await queryClient.invalidateQueries({ queryKey: ['curriculum'] });
    });

    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(
      Array.from(document.querySelectorAll('[data-entry-id]'), (entry) => entry.getAttribute('data-entry-id')),
    ).toEqual(['demo', 'third', 'second']);
    expect(screen.getByText('Unsaved edits')).toBeInTheDocument();
  });

  it('test_curriculum_chat_given_context_not_sent_by_endpoint_expect_context_actions_hidden', () => {
    renderAt('/');

    expect(screen.queryByRole('combobox', { name: 'Add context' })).not.toBeInTheDocument();
  });

  it('test_curriculum_tab_given_entries_expect_plan_order_status_chips_and_first_selected', () => {
    renderAt('/');

    const titles = screen
      .getAllByRole('button', { pressed: undefined })
      .filter((button) => button.hasAttribute('aria-pressed'));
    expect(titles.map((button) => button.textContent)).toEqual([
      expect.stringContaining('Demo lesson'),
      expect.stringContaining('Second lesson'),
      expect.stringContaining('Third lesson'),
    ]);
    expect(screen.getByRole('heading', { name: 'Demo lesson', level: 3 })).toBeInTheDocument();
    expect(screen.getByText('findings 2')).toBeInTheDocument();
  });

  it('test_curriculum_reorder_given_alt_arrow_up_expect_entry_moved_focus_kept_save_enabled', async () => {
    renderAt('/');
    const third = screen.getByRole('button', { name: 'Open Third lesson details' });

    third.focus();
    await userEvent.keyboard('{Alt>}{ArrowUp}{/Alt}');
    await new Promise((resolve) => requestAnimationFrame(resolve));

    const order = Array.from(document.querySelectorAll('[data-entry-id]')).map((item) =>
      item.getAttribute('data-entry-id'),
    );
    expect(order).toEqual(['demo', 'third', 'second']);
    expect(document.activeElement).toHaveTextContent('Third lesson');
    expect(screen.getByRole('button', { name: 'Save curriculum' })).toBeEnabled();
  });

  it('test_entry_detail_given_selected_entry_expect_its_dependencies', async () => {
    renderAt('/');

    await userEvent.click(screen.getByRole('button', { name: 'Open Second lesson details' }));

    expect(screen.getByRole('heading', { name: 'Second lesson', level: 3 })).toBeInTheDocument();
    expect(screen.getByText('demo')).toBeInTheDocument();
  });

  it('test_staged_removal_given_chat_refetch_expect_staged_order_and_fresh_metadata_preserved', async () => {
    const data = curriculumPayload();
    const refreshed = {
      ...data,
      lessons: data.lessons.map((lesson) =>
        lesson.lessonId === 'third' ? { ...lesson, source: 'draft' as const } : lesson,
      ),
    };
    const removalPreview = {
      reviewDigest: 'remove-review',
      entries: data.lessons
        .filter((lesson) => lesson.lessonId !== 'second')
        .map((lesson, sequenceIndex) => ({
          sequenceIndex,
          catalogId: lesson.lessonId,
          title: lesson.title,
          learnerOutcome: data.entries[lesson.lessonId].learnerOutcome,
          cefrLevel: lesson.cefrLevel,
          prerequisites: lesson.lessonId === 'third' ? [] : data.entries[lesson.lessonId].dependsOn,
          helpfulPrerequisites: [],
        })),
      affectedLessons: [],
      movedOutcomes: [],
      movedPrerequisites: [],
      dependencyFindings: [],
    };
    const fetch = vi.fn(async (path: string) => {
      if (path === '/api/curriculum/preview')
        return new Response(JSON.stringify(removalPreview), { headers: { 'Content-Type': 'application/json' } });
      if (path === '/api/curriculum/chat')
        return new Response(JSON.stringify({ job_id: 'chat-job', turn_id: 'turn-1' }), {
          headers: { 'Content-Type': 'application/json' },
        });
      if (path === '/api/jobs/chat-job')
        return new Response(JSON.stringify({ status: 'done', result: {} }), {
          headers: { 'Content-Type': 'application/json' },
        });
      if (path === '/api/curriculum')
        return new Response(JSON.stringify(refreshed), { headers: { 'Content-Type': 'application/json' } });
      return new Response(JSON.stringify({}), { headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/', { curriculum: data });

    await userEvent.click(screen.getByRole('checkbox', { name: 'Select Second lesson' }));
    await userEvent.click(screen.getByRole('button', { name: 'Remove…' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Accept removal' })).toBeEnabled());
    await userEvent.click(screen.getByRole('button', { name: 'Accept removal' }));
    expect(
      Array.from(document.querySelectorAll('[data-entry-id]')).map((item) => item.getAttribute('data-entry-id')),
    ).toEqual(['demo', 'third']);

    await userEvent.type(screen.getByRole('textbox', { name: 'Message to chat' }), 'Check the staged curriculum.');
    await userEvent.click(screen.getByRole('button', { name: 'Send' }));

    const thirdRow = await waitFor(() => {
      const row = document.querySelector<HTMLLIElement>('[data-entry-id="third"]');
      if (!row) throw new Error('third curriculum row was not rendered');
      expect(row).toHaveTextContent('Draft');
      return row;
    });
    expect(within(thirdRow).getByRole('button', { name: 'Open Third lesson details' })).toBeInTheDocument();
    expect(
      Array.from(document.querySelectorAll('[data-entry-id]')).map((item) => item.getAttribute('data-entry-id')),
    ).toEqual(['demo', 'third']);

    await userEvent.click(screen.getByRole('button', { name: 'Save curriculum' }));
    await screen.findByRole('heading', { name: 'Save curriculum changes' });
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith(
        '/api/curriculum/preview',
        expect.objectContaining({
          body: JSON.stringify({
            edits: [{ kind: 'remove', lesson_ids: ['second'], delete_never_promoted_drafts: false }],
          }),
        }),
      ),
    );
  });

  it('test_persisted_active_chat_given_page_reload_expect_monitoring_cancel_and_terminal_refresh', async () => {
    const data = curriculumPayload();
    const active = {
      ...data,
      chat: [
        chatTurnFixture({
          role: 'author',
          text: 'Please review the entries.',
          turnId: 'restored-turn',
          status: 'running',
          partial: 'Provisional response from server.',
          kind: 'curriculum',
        }),
      ],
    };
    const cancelled = {
      ...active,
      chat: active.chat.map((turn) => ({ ...turn, status: 'cancelled' as const, partial: '' })),
    };
    let curriculumReads = 0;
    const fetch = vi.fn(async (path: string) => {
      if (path === '/api/curriculum') {
        curriculumReads += 1;
        return new Response(JSON.stringify(curriculumReads === 1 ? active : cancelled), {
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return new Response(JSON.stringify({}), { headers: { 'Content-Type': 'application/json' } });
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/', { curriculum: active });

    expect(screen.getByRole('status', { name: 'Codex response in progress' })).toHaveTextContent(
      'Provisional response from server.',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(fetch).toHaveBeenCalledWith(
      '/api/curriculum/chat/restored-turn/cancel',
      expect.objectContaining({ method: 'POST' }),
    );
    await waitFor(() => expect(screen.getByText('Cancelled')).toBeInTheDocument(), { timeout: 3000 });
    expect(curriculumReads).toBeGreaterThanOrEqual(2);
  });
});
