import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { renderAt, workbenchSettingsPayload } from '@/test/fixtures';

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

afterEach(() => vi.unstubAllGlobals());

describe('Workbench settings', () => {
  it('test_settings_save_given_newer_typing_while_pending_expect_newer_draft_preserved', async () => {
    const defaults = workbenchSettingsPayload();
    let finishSave: (() => void) | undefined;
    let submitted: typeof defaults.settings | undefined;
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method !== 'PUT') return json(defaults);
      submitted = JSON.parse(String(init.body)) as typeof defaults.settings;
      return new Promise<Response>((resolve) => {
        finishSave = () => resolve(json({ ...defaults, settings: submitted }));
      });
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/settings');

    await screen.findByRole('heading', { name: 'Models by role' });
    const monthlyCap = screen.getByRole('spinbutton', { name: 'Monthly audio cap in USD' });
    await userEvent.clear(monthlyCap);
    await userEvent.type(monthlyCap, '10');
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    await waitFor(() => expect(submitted?.audio.monthly_cap_usd).toBe(10));
    await userEvent.clear(monthlyCap);
    await userEvent.type(monthlyCap, '20');

    await act(async () => finishSave?.());

    await screen.findByText('Settings saved.');
    expect(monthlyCap).toHaveValue(20);
  });

  it('test_settings_page_given_duplicate_model_names_expect_provider_labels_and_short_unique_names', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => json(workbenchSettingsPayload())),
    );
    renderAt('/settings');

    await screen.findByRole('heading', { name: 'Models by role' });
    await userEvent.click(screen.getByRole('combobox', { name: 'Generation model' }));

    expect(await screen.findByRole('option', { name: 'glm-5.3 (zai-coding-plan)' })).toBeVisible();
    expect(screen.getByRole('option', { name: 'glm-5.3 (opencode-go)' })).toBeVisible();
    expect(screen.getByRole('option', { name: 'gpt-6-luna' })).toBeVisible();
  });

  it('test_settings_page_given_codex_chat_choice_expect_codex_options_and_override_saved', async () => {
    const defaultPayload = workbenchSettingsPayload();
    let responseBody = defaultPayload;
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === 'PUT') {
        const saved = JSON.parse(String(init.body)) as typeof defaultPayload.settings;
        responseBody = { ...defaultPayload, settings: saved };
        return json(responseBody);
      }
      return json(responseBody);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/settings');

    await screen.findByRole('heading', { name: 'Models by role' });
    await userEvent.click(screen.getByRole('combobox', { name: 'Chat model' }));
    expect(await screen.findByRole('option', { name: 'gpt-6-luna' })).toBeVisible();
    expect(screen.queryByRole('option', { name: 'glm-5.3' })).not.toBeInTheDocument();
    expect(screen.queryByRole('option', { name: 'claude-sonnet-5-5' })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('option', { name: 'gpt-6-luna' }));
    await userEvent.click(screen.getByRole('combobox', { name: 'Chat reasoning effort' }));
    await userEvent.click(await screen.findByRole('option', { name: 'low' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }));

    const savedBody = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')?.[1]?.body;
    await waitFor(() =>
      expect(JSON.parse(String(savedBody)).roles.chat).toEqual({ model: 'gpt-6-luna', effort: 'low' }),
    );
  });

  it('test_settings_page_given_save_and_reset_expect_workspace_values_persist_and_restore_defaults', async () => {
    const defaultPayload = workbenchSettingsPayload();
    let responseBody = defaultPayload;
    const fetch = vi.fn(async (_path: string, init?: RequestInit) => {
      if (init?.method === 'PUT') {
        const saved = JSON.parse(String(init.body)) as typeof defaultPayload.settings;
        responseBody = { ...defaultPayload, settings: saved };
        return json(responseBody);
      }
      if (init?.method === 'POST') {
        responseBody = defaultPayload;
        return json(responseBody);
      }
      return json(responseBody);
    });
    vi.stubGlobal('fetch', fetch);
    renderAt('/settings');

    await screen.findByRole('heading', { name: 'Models by role' });
    await userEvent.click(screen.getByRole('combobox', { name: 'Generation model' }));
    await userEvent.click(await screen.findByRole('option', { name: 'gpt-6-luna' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }));

    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith('/api/settings', expect.objectContaining({ method: 'PUT' })),
    );
    const savedBody = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')?.[1]?.body;
    expect(JSON.parse(String(savedBody)).roles.generation.model).toBe('openai/gpt-6-luna');

    await userEvent.click(screen.getByRole('button', { name: 'Reset to defaults' }));
    await waitFor(() =>
      expect(fetch).toHaveBeenCalledWith('/api/settings/reset', expect.objectContaining({ method: 'POST' })),
    );
    expect(screen.getByRole('combobox', { name: 'Generation model' })).toHaveTextContent('gpt-6.1-sol');
  });
});
