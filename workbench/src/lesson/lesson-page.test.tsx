import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
  curriculumPayload,
  HOSTILE,
  lessonPayload,
  renderAt,
  renderAtCold,
  workbenchSettingsPayload,
} from '@/test/fixtures';

afterEach(() => {
  vi.unstubAllGlobals();
  window.sessionStorage.removeItem('regeneration-demo');
});

describe('Lesson page', () => {
  it('test_staleness_banner_given_unparseable_exercises_yaml_expect_unknown_reason', () => {
    const lesson = lessonPayload();
    lesson.elements = [{ kind: 'exercise', id: 'ex1', operation: 'choose', prompt: [], payload: {} }];
    lesson.state.exerciseStaleness = [
      {
        status: 'unknown',
        exerciseKey: null,
        reason: 'exercises.yaml could not be parsed: line 42: unexpected indent',
        history: [],
      },
    ];
    renderAt('/lessons/demo', { lesson });

    expect(screen.getByRole('heading', { name: 'Staleness unavailable' })).toBeInTheDocument();
    expect(screen.getByRole('status', { name: '' })).toHaveTextContent(
      'exercises.yaml could not be parsed: line 42: unexpected indent',
    );
  });
  it('test_source_findings_given_source_view_expect_named_region', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    await userEvent.click(screen.getByRole('radio', { name: 'Source' }));

    expect(screen.getByRole('region', { name: 'Source check findings' })).toBeInTheDocument();
  });
  it('test_lesson_views_given_plan_and_source_expect_source_text_instead_of_rows', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });

    await userEvent.click(screen.getByRole('radio', { name: 'Source' }));
    expect(screen.getByRole('textbox', { name: 'Lesson source' })).toHaveValue('# Demo source');
    expect(document.getElementById('intro-b0')).toBeNull();

    await userEvent.click(screen.getByRole('radio', { name: 'Plan' }));
    expect(screen.getByText('plan: demo')).toBeInTheDocument();
  });
  it('test_lesson_page_given_hostile_lesson_and_state_text_expect_text_not_markup', () => {
    const base = lessonPayload();
    const lesson = {
      ...base,
      lesson: {
        title: HOSTILE,
        cefrLevel: 'A1',
        kind: 'grammar',
        goal: HOSTILE,
      },
      elements: [
        {
          kind: 'section',
          id: 'intro',
          anchor_id: 'intro',
          title: HOSTILE,
          blocks: [{ kind: 'paragraph', spans: [{ kind: 'text', value: HOSTILE }] }],
        },
      ],
      state: {
        ...base.state,
        proposals: [],
        findings: [
          {
            ...base.state.findings[0],
            target: { elementId: 'intro', blockIndex: 0 },
            code: HOSTILE,
            explanation: HOSTILE,
          },
        ],
      },
    };
    const { container } = renderAt('/lessons/demo', { lesson });

    expect(container.querySelector('img')).toBeNull();
    expect(screen.getAllByText(HOSTILE).length).toBeGreaterThanOrEqual(4);
  });
  it('test_lesson_page_given_no_elements_expect_compile_error_state_and_source_errors', () => {
    renderAt('/lessons/demo', {
      lesson: lessonPayload({
        elements: [],
        errors: ['compile preview: exercises.yaml is malformed'],
      }),
    });

    expect(screen.getByText('1 source finding')).toBeInTheDocument();
    expect(screen.getByText('compile preview: exercises.yaml is malformed')).toBeInTheDocument();
    expect(screen.getByText('This lesson could not be compiled for display.')).toBeInTheDocument();
  });
  it('test_lesson_route_given_cold_cache_expect_lesson_and_settings_queries_started', async () => {
    const lesson = lessonPayload();
    const requestedPaths: string[] = [];
    const pendingResponses = new Map<string, (response: Response) => void>();
    const fetch = vi.fn((path: string) => {
      requestedPaths.push(path);
      return new Promise<Response>((resolve) => pendingResponses.set(path, resolve));
    });
    vi.stubGlobal('fetch', fetch);
    renderAtCold('/lessons/demo');

    await waitFor(() => {
      expect(requestedPaths).toEqual(expect.arrayContaining(['/api/lessons/demo', '/api/settings', '/api/curriculum']));
    });
    expect(fetch).toHaveBeenCalledWith('/api/lessons/demo', expect.objectContaining({ cache: 'no-store' }));
    expect(fetch).toHaveBeenCalledWith('/api/settings', expect.objectContaining({ cache: 'no-store' }));

    pendingResponses.get('/api/lessons/demo')?.(
      new Response(JSON.stringify(lesson), { headers: { 'Content-Type': 'application/json' } }),
    );
    pendingResponses.get('/api/settings')?.(
      new Response(JSON.stringify(workbenchSettingsPayload()), { headers: { 'Content-Type': 'application/json' } }),
    );
    pendingResponses.get('/api/curriculum')?.(
      new Response(JSON.stringify(curriculumPayload()), { headers: { 'Content-Type': 'application/json' } }),
    );
    expect(await screen.findByRole('heading', { name: 'Demo lesson' })).toBeInTheDocument();
  });
});
