import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { PacketElement } from '@/api/contracts';
import { curriculumPayload, lessonPayload, renderAt, rowElement } from '@/test/fixtures';

function lessonWithBlocks(blocks: Record<string, unknown>[]) {
  const lesson = lessonPayload();
  const section: PacketElement = {
    kind: 'section',
    id: 'intro',
    anchor_id: 'intro',
    title: 'Intro',
    role: 'orient',
    blocks,
  };
  return {
    ...lesson,
    elements: [section],
    state: { ...lesson.state, findings: [], proposals: [] },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.removeItem('regeneration-demo');
});

describe('Lesson editing', () => {
  it('test_edit_card_given_changed_dialogue_line_expect_per_type_fields_and_unsaved_state', async () => {
    const updated = lessonPayload({
      draft: {
        exists: true,
        startedFrom: 'digest',
        dirty: true,
        lastCommit: null,
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((_path, options) =>
        Promise.resolve(
          new Response(
            JSON.stringify(
              options?.method === 'PUT'
                ? {
                    lesson: updated,
                    check: { findings: [], errors: [], notes: [] },
                  }
                : String(_path).includes('curriculum')
                  ? curriculumPayload()
                  : updated,
            ),
            { status: 200 },
          ),
        ),
      ),
    );
    updated.elements = [
      {
        kind: 'section',
        id: 'intro',
        anchor_id: 'intro',
        title: 'Intro',
        role: 'orient',
        blocks: [
          {
            kind: 'reading',
            speaker_name: 'Jonas',
            spans: [{ kind: 'text', value: 'Jeg lager kaffe nå.' }],
            translation: 'I make coffee.',
          },
        ],
      },
    ];
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    await userEvent.click(
      within(rowElement('intro-b0')).getByRole('button', {
        name: 'Edit block',
      }),
    );
    expect(screen.getByLabelText('Speaker')).toHaveValue('Jonas');
    expect(screen.getByLabelText('Norwegian')).toHaveAttribute('lang', 'nb');
    await userEvent.clear(screen.getByLabelText('Translation'));
    await userEvent.type(screen.getByLabelText('Translation'), 'I make coffee.');
    await userEvent.click(screen.getByRole('button', { name: 'Done' }));

    await waitFor(() => expect(screen.getByText('Uncommitted draft')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
    const blockActions = within(rowElement('intro-b0')).getByLabelText('Block actions');
    expect(within(blockActions).getByText('edited')).toBeInTheDocument();
    expect(within(blockActions).getByRole('button', { name: 'Edit block' })).toBeEnabled();
    expect(within(rowElement('intro-b0')).getByText('I make coffee.')).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledWith(
      '/api/lessons/demo/blocks/intro%230',
      expect.objectContaining({
        method: 'PUT',
        headers: expect.objectContaining({
          'X-Workbench-Request': '1',
          'Content-Type': 'application/json',
        }),
        body: JSON.stringify({
          fields: {
            speaker_name: 'Jonas',
            body: 'Jeg lager kaffe nå.',
            translation: 'I make coffee.',
          },
        }),
      }),
    );
  });

  it('test_client_dirty_block_editor_given_draft_differs_from_canonical_expect_promotion_blocked_until_write_finishes', async () => {
    const source = lessonPayload({
      state: { ...lessonPayload().state },
    });
    source.draft.exists = true;
    const updated = lessonPayload({
      source: 'draft',
      sourceLabel: 'drafts/demo',
      draft: { exists: true, startedFrom: 'canonical-digest', dirty: true, lastCommit: null },
      state: { ...lessonPayload().state },
    });
    updated.elements = [
      {
        kind: 'section',
        id: 'intro',
        anchor_id: 'intro',
        title: 'Intro',
        role: 'orient',
        blocks: [
          {
            kind: 'reading',
            speaker_name: 'Jonas',
            spans: [{ kind: 'text', value: 'Jeg lager kaffe nå.' }],
            translation: 'I make coffee.',
          },
        ],
      },
    ];
    const fetch = vi
      .fn()
      .mockImplementation((path, options) =>
        Promise.resolve(
          new Response(
            JSON.stringify(
              options?.method === 'PUT'
                ? { lesson: updated, check: { findings: [], errors: [], notes: [] } }
                : String(path).includes('curriculum')
                  ? curriculumPayload()
                  : updated,
            ),
            { status: 200 },
          ),
        ),
      );
    vi.stubGlobal('fetch', fetch);
    renderAt('/lessons/demo', { lesson: source });

    await userEvent.click(within(rowElement('intro-b0')).getByRole('button', { name: 'Edit block' }));
    await userEvent.clear(screen.getByLabelText('Translation'));
    await userEvent.type(screen.getByLabelText('Translation'), 'I make coffee.');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Promote' })).toBeDisabled());
    const promote = screen.getByRole('button', { name: 'Promote' });
    expect(promote).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'More lesson actions' }));
    expect(screen.getByRole('menuitem', { name: 'Promote…' })).toHaveAttribute('aria-disabled', 'true');
    await userEvent.keyboard('{Escape}');

    await userEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(screen.getByText('Uncommitted draft')).toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'Promote' })).toBeEnabled();
  });

  it('test_edit_card_given_unchanged_fields_and_cancel_expect_lesson_still_committed', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    await userEvent.click(
      within(rowElement('intro-b0')).getByRole('button', {
        name: 'Edit block',
      }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(screen.getByText('Committed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it.each(['list', 'word_list', 'table', 'callout'])(
    'test_edit_card_given_%s_block_expect_raw_source_editor',
    async (kind) => {
      const lesson = lessonWithBlocks([{ kind }]);
      lesson.blocks = [
        {
          key: 'intro#0',
          kind,
          source: `::: ${kind}\nExact source\n:::\n`,
          fields: {},
        },
      ];
      renderAt('/lessons/demo', { lesson });
      await userEvent.click(
        within(rowElement('intro-b0')).getByRole('button', {
          name: 'Edit block',
        }),
      );
      expect(screen.getByRole('textbox', { name: 'Block source' })).toHaveValue(`::: ${kind}\nExact source\n:::\n`);
      expect(screen.queryByRole('tab', { name: 'Form' })).toBeNull();
    },
  );

  it.each(['cancel', 'done', 'escape'])(
    'test_edit_card_close_given_%s_expect_focus_returns_to_edit_button',
    async (closeMethod) => {
      renderAt('/lessons/demo', { lesson: lessonPayload() });
      const editButton = within(rowElement('intro-b0')).getByRole('button', { name: 'Edit block' });

      await userEvent.click(editButton);
      if (closeMethod === 'escape') {
        await userEvent.keyboard('{Escape}');
      } else {
        await userEvent.click(screen.getByRole('button', { name: closeMethod === 'cancel' ? 'Cancel' : 'Done' }));
      }

      await waitFor(() =>
        expect(within(rowElement('intro-b0')).getByRole('button', { name: 'Edit block' })).toHaveFocus(),
      );
    },
  );

  it('test_edit_card_switch_given_dirty_open_block_expect_confirmation_before_discard', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    await userEvent.click(
      within(rowElement('intro-b0')).getByRole('button', {
        name: 'Edit block',
      }),
    );
    await userEvent.type(screen.getByLabelText('Translation'), ' Typed.');

    await userEvent.click(
      within(rowElement('intro-b1')).getByRole('button', {
        name: 'Edit block',
      }),
    );
    expect(
      screen.getByRole('dialog', {
        name: 'Discard changes to the open block?',
      }),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    await waitFor(() => expect(screen.getByLabelText('Translation')).toHaveFocus());
    expect(screen.getByLabelText('Translation')).toHaveValue("I'm making coffee. Typed.");

    await userEvent.click(
      within(rowElement('intro-b1')).getByRole('button', {
        name: 'Edit block',
      }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Discard and continue' }));
    expect(screen.queryByLabelText('Translation')).toBeNull();
    expect(screen.getByLabelText('Text')).toHaveValue('Second block');
    await waitFor(() => expect(screen.getByLabelText('Text')).toHaveFocus());
  });

  it.each([
    ['Source', '# Demo source'],
    ['Plan', 'plan: demo'],
  ])('test_view_switch_to_%s_given_dirty_open_block_expect_confirmation_then_switch', async (label, sourceText) => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    await userEvent.click(
      within(rowElement('intro-b0')).getByRole('button', {
        name: 'Edit block',
      }),
    );
    await userEvent.type(screen.getByLabelText('Translation'), ' Typed.');

    await userEvent.click(screen.getByRole('radio', { name: label }));
    expect(
      screen.getByRole('dialog', {
        name: 'Discard changes to the open block?',
      }),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Keep editing' }));
    expect(screen.getByLabelText('Translation')).toHaveValue("I'm making coffee. Typed.");
    expect(screen.queryByText(sourceText)).toBeNull();

    await userEvent.click(screen.getByRole('radio', { name: label }));
    await userEvent.click(screen.getByRole('button', { name: 'Discard and continue' }));
    if (label === 'Source') expect(screen.getByRole('textbox', { name: 'Lesson source' })).toHaveValue(sourceText);
    else expect(screen.getByText(sourceText)).toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Edit block' })).toBeNull();
  });

  it('test_edit_card_switch_given_clean_open_block_expect_no_confirmation', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    await userEvent.click(
      within(rowElement('intro-b0')).getByRole('button', {
        name: 'Edit block',
      }),
    );

    await userEvent.click(
      within(rowElement('intro-b1')).getByRole('button', {
        name: 'Edit block',
      }),
    );

    expect(screen.queryByRole('dialog')).toBeNull();
    expect(screen.getByLabelText('Text')).toHaveValue('Second block');
  });

  it('test_block_write_given_api_failure_expect_typed_text_retained_and_error_toast', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({ error: 'write failed' }), {
            status: 409,
          }),
        ),
      ),
    );
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    await userEvent.click(
      within(rowElement('intro-b0')).getByRole('button', {
        name: 'Edit block',
      }),
    );
    await userEvent.type(screen.getByLabelText('Translation'), ' Keep this.');
    await userEvent.click(screen.getByRole('button', { name: 'Done' }));
    await screen.findByText('409 write failed');
    expect(screen.getByLabelText('Translation')).toHaveValue("I'm making coffee. Keep this.");
    expect(screen.getByRole('button', { name: 'Done' })).toBeEnabled();
    expect(screen.getByText('Committed')).toBeInTheDocument();
  });

  it('test_block_done_given_unchanged_source_expect_no_write_request', async () => {
    vi.stubGlobal('fetch', vi.fn());
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    await userEvent.click(
      within(rowElement('intro-b0')).getByRole('button', {
        name: 'Edit block',
      }),
    );
    await userEvent.click(screen.getByRole('tab', { name: 'Source' }));
    expect(screen.getByRole('textbox', { name: 'Block source' })).toHaveValue(lessonPayload().blocks[1]?.source);
    await userEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(fetch).not.toHaveBeenCalled();
    expect(screen.getByText('Committed')).toBeInTheDocument();
  });

  it('test_exercise_edit_given_source_change_expect_yaml_item_sent_to_row_key', async () => {
    const updated = lessonPayload({
      draft: {
        exists: true,
        startedFrom: 'digest',
        dirty: true,
        lastCommit: null,
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((path, options) =>
        Promise.resolve(
          new Response(
            JSON.stringify(
              options?.method === 'PUT'
                ? {
                    lesson: updated,
                    check: { findings: [], errors: [], notes: [] },
                  }
                : String(path).includes('curriculum')
                  ? curriculumPayload()
                  : updated,
            ),
            { status: 200 },
          ),
        ),
      ),
    );
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    await userEvent.click(within(rowElement('ex1')).getByRole('button', { name: 'Edit exercise' }));
    expect(screen.queryByRole('tab', { name: 'Form' })).toBeNull();
    await userEvent.clear(screen.getByRole('textbox', { name: 'Block source' }));
    await userEvent.type(
      screen.getByRole('textbox', { name: 'Block source' }),
      '- handle: ex1\n  op: judge\n  sentence_md: Hei.',
    );
    await userEvent.click(screen.getByRole('button', { name: 'Done' }));
    await waitFor(() => expect(screen.queryByRole('group', { name: 'Edit block' })).toBeNull());
    expect(fetch).toHaveBeenCalledWith(
      '/api/lessons/demo/blocks/ex1',
      expect.objectContaining({
        method: 'PUT',
        body: JSON.stringify({
          source: '- handle: ex1\n  op: judge\n  sentence_md: Hei.',
        }),
      }),
    );
  });

  it('test_source_autosave_given_edits_during_debounce_expect_last_text_written_then_commit_status', async () => {
    const updated = lessonPayload({
      source: 'draft',
      lessonSource: 'New source.',
      draft: {
        exists: true,
        startedFrom: 'digest',
        dirty: true,
        lastCommit: null,
      },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((path, options) => {
        if (options?.method === 'POST') {
          updated.draft = {
            ...updated.draft,
            dirty: false,
            lastCommit: 'abcdef1234567890',
          };
          return Promise.resolve(
            new Response(
              JSON.stringify({
                commit: 'abcdef1234567890',
                check: { findings: [], errors: [], notes: [] },
              }),
              { status: 200 },
            ),
          );
        }
        return Promise.resolve(
          new Response(
            JSON.stringify(
              options?.method === 'PUT'
                ? {
                    lesson: updated,
                    check: { findings: [], errors: [], notes: [] },
                  }
                : String(path).includes('curriculum')
                  ? curriculumPayload()
                  : updated,
            ),
            { status: 200 },
          ),
        );
      }),
    );
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    await userEvent.click(screen.getByRole('radio', { name: 'Source' }));
    await userEvent.clear(screen.getByRole('textbox', { name: 'Lesson source' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Lesson source' }), 'New source.');
    expect(screen.getByText('Saving…')).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();
    await screen.findByText('Saved to draft', {}, { timeout: 2000 });
    expect(fetch).toHaveBeenCalledWith(
      '/api/lessons/demo/files/lesson.md',
      expect.objectContaining({
        body: JSON.stringify({ text: 'New source.' }),
      }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('Saved abcdef1');
    expect(screen.getByText('Committed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('test_source_autosave_given_api_failure_expect_text_retained_and_retry_control', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(() =>
        Promise.resolve(
          new Response(JSON.stringify({ error: 'disk unavailable' }), {
            status: 503,
          }),
        ),
      ),
    );
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    await userEvent.click(screen.getByRole('radio', { name: 'Source' }));
    await userEvent.clear(screen.getByRole('textbox', { name: 'Lesson source' }));
    await userEvent.type(screen.getByRole('textbox', { name: 'Lesson source' }), 'Keep my source');
    await screen.findByText('Draft write failed', {}, { timeout: 2000 });
    expect(screen.getByRole('textbox', { name: 'Lesson source' })).toHaveValue('Keep my source');
    expect(screen.getByRole('button', { name: 'Retry draft write' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
  });

  it('test_source_refresh_given_failed_get_after_write_expect_newer_typed_text_kept', async () => {
    let finishWrite: ((response: Response) => void) | undefined;
    const updated = lessonPayload({
      source: 'draft',
      lessonSource: 'First draft',
      draft: { exists: true, startedFrom: 'digest', dirty: true, lastCommit: null },
    });
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation((path, options) => {
        if (options?.method === 'PUT')
          return new Promise<Response>((resolve) => {
            finishWrite = resolve;
          });
        return Promise.resolve(
          new Response(
            JSON.stringify(
              String(path).includes('curriculum') ? curriculumPayload() : { error: 'refresh unavailable' },
            ),
            {
              status: String(path).includes('curriculum') ? 200 : 503,
            },
          ),
        );
      }),
    );
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    await userEvent.click(screen.getByRole('radio', { name: 'Source' }));
    const source = screen.getByRole('textbox', { name: 'Lesson source' });
    await userEvent.clear(source);
    await userEvent.type(source, 'First draft');
    await waitFor(() => expect(finishWrite).toBeDefined(), { timeout: 2000 });
    await userEvent.clear(source);
    await userEvent.type(source, 'Keep newer text');
    await act(async () =>
      finishWrite?.(
        new Response(JSON.stringify({ lesson: updated, check: { findings: [], errors: [], notes: [] } }), {
          status: 200,
        }),
      ),
    );
    await screen.findByText(
      'Refresh failed. Your editor text is still here; retry by reloading after writing your draft.',
    );
    expect(screen.getByRole('textbox', { name: 'Lesson source' })).toHaveValue('Keep newer text');
  });

  it('test_save_given_commit_refusal_expect_dirty_status_retained_and_error_toast', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockImplementation(() =>
          Promise.resolve(new Response(JSON.stringify({ error: 'unrelated paths are staged' }), { status: 409 })),
        ),
    );
    renderAt('/lessons/demo', {
      lesson: lessonPayload({ draft: { exists: true, startedFrom: 'digest', dirty: true, lastCommit: null } }),
    });
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    await screen.findByText('409 unrelated paths are staged');
    expect(screen.getByText('Uncommitted draft')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeEnabled();
  });
});
