import { QueryClient } from '@tanstack/react-query';
import { render } from '@testing-library/react';

import type { CurriculumPayload, LessonPayload, WorkbenchSettingsResponse } from '@/api/contracts';
import { App } from '@/app/app';
import { LessonPage } from '@/lesson/lesson-page';

import { chatTurnFixture, findingFixture, proposalFixture } from './wire-fixtures';

export const HOSTILE = '<img src=x onerror=alert(1)>';

export function lessonPayload(overrides: Partial<LessonPayload> = {}): LessonPayload {
  return {
    lessonId: 'demo',
    blocks: [
      {
        key: 'intro',
        kind: 'heading',
        source: '## Intro {#intro}\n\n',
        fields: { title: 'Intro' },
      },
      {
        key: 'intro#0',
        kind: 'reading',
        source: '::: {.reading speaker_name="Jonas" translation="I make coffee."}\nJeg lager kaffe nå.\n:::\n\n',
        fields: {
          speaker_name: 'Jonas',
          body: 'Jeg lager kaffe nå.',
          translation: "I'm making coffee.",
        },
      },
      {
        key: 'intro#1',
        kind: 'paragraph',
        source: 'Second block\n',
        fields: { text: 'Second block' },
      },
      {
        key: 'ex1',
        kind: 'exercise',
        source: '- handle: ex1\n  op: judge\n',
        fields: {},
      },
    ],
    draft: { exists: false, startedFrom: null, dirty: false, lastCommit: null },
    source: 'canonical',
    sourceLabel: 'content/lessons/demo',
    lessonSource: '# Demo source',
    planSource: 'plan: demo',
    planPreview: { frontmatter: {}, body: '', parseError: 'source file must start with YAML front matter' },
    lesson: {
      title: 'Demo lesson',
      cefrLevel: 'A1',
      kind: 'grammar',
      goal: 'Learn the demo.',
    },
    elements: [
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
            translation: "I'm making coffee.",
          },
          {
            kind: 'paragraph',
            spans: [{ kind: 'text', value: 'Second block' }],
          },
        ],
      },
      {
        kind: 'exercise',
        id: 'ex1',
        anchor_id: 'ex1',
        operation: 'judge',
        objective_id: 'o',
        prompt: [{ kind: 'text', value: 'Judge it' }],
        payload: { sentence: [{ kind: 'text', value: 'Hei.' }], is_correct: true, feedback: 'Hei is a greeting.' },
      },
    ],
    sourceFindings: [],
    errors: [],
    state: {
      findings: [
        findingFixture({
          findingId: 'f1',
          target: { elementId: 'intro', blockIndex: 1 },
          origin: 'ai',
          severity: 'warning',
          advisory: true,
          status: 'open',
          code: 'gloss-code',
          excerpt: 'Second block',
          explanation: 'Gloss note',
        }),
        findingFixture({
          findingId: 'f2',
          target: { elementId: 'ex1', blockIndex: null },
          origin: 'check',
          severity: 'error',
          advisory: false,
          status: 'open',
          code: 'check-code',
          excerpt: 'Judge it',
          explanation: 'Check note',
        }),
      ],
      proposals: [
        proposalFixture({
          proposalId: 'p1',
          key: 'intro#0',
          status: 'pending',
          check: { status: 'passed', errors: [] },
          target: { elementId: 'intro', blockIndex: 0 },
          before: 'Before text',
          after: 'After text',
          note: 'Why',
          allowedDecisions: ['accept', 'reject'],
        }),
      ],
      chat: [
        chatTurnFixture({
          role: 'assistant',
          text: 'Hello from chat',
        }),
      ],
      activeTurn: null,
      statusChips: [{ label: 'baseline', tone: 'neutral' }],
      approveBlockers: [{ kind: 'open_proposal', stage: 'lesson', target: 'proposal' }],
      approvalStages: [],
      approvable: true,
      exerciseStaleness: [],
      promotable: true,
      promotionBlockers: [],
      proposalCheck: null,
      reviewSummary: null,
      reviewCreatedAt: null,
      reviewDigest: null,
      reviewCoverage: { covered: 0, total: 0, uncovered: [] },
      checkFindingCount: 0,
      aiFindingCount: 0,
      explanations: {},
      approvalChanges: {},
    },
    audio: {
      clips: [
        {
          binding: 'section:intro:block:0',
          rowKey: 'intro#0',
          fingerprint: 'a'.repeat(64),
          audioId: 'audio-demo',
          filename: 'demo.wav',
          text: 'Jeg lager kaffe nå.',
          translation: "I'm making coffee.",
          voice: 'nb-NO-Finn',
          speakerId: 'jonas',
          speakerName: 'Jonas',
          origin: 'lesson_reading',
          characters: 19,
          status: 'ready',
          durationMs: 2100,
          playUrl: '/api/lessons/demo/audio/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
          findings: [],
        },
      ],
      voices: [
        {
          speakerId: 'jonas',
          speakerName: 'Jonas',
          voice: 'nb-NO-Finn',
          changedSinceRelease: false,
          releasedVoice: 'nb-NO-Finn',
        },
      ],
      missingCount: 0,
      staleCount: 0,
      readyCount: 1,
      blockers: [],
    },
    ...overrides,
  };
}

export function rowElement(id: string): HTMLElement {
  const element = document.getElementById(id);
  if (!element) throw new Error(`row ${id} is not rendered`);
  return element;
}

export function curriculumPayload(): CurriculumPayload {
  return {
    curriculumApproval: 'auto_approved',
    lessons: [
      {
        lessonId: 'demo',
        title: 'Demo lesson',
        cefrLevel: 'A1',
        sequenceIndex: 0,
        source: 'canonical',
      },
      {
        lessonId: 'second',
        title: 'Second lesson',
        cefrLevel: 'A1',
        sequenceIndex: 1,
        source: 'draft',
      },
      {
        lessonId: 'third',
        title: 'Third lesson',
        cefrLevel: 'A2',
        sequenceIndex: 2,
        source: 'canonical',
      },
    ],
    entries: {
      demo: {
        learnerOutcome: 'Use the skill.',
        cefrLevel: 'A1',
        findings: [],
        statusChips: [{ label: 'baseline', tone: 'neutral' }],
        dependsOn: [],
        helpfulPrerequisites: [],
      },
      second: {
        learnerOutcome: 'Use the skill.',
        cefrLevel: 'A1',
        findings: [],
        statusChips: [{ label: 'edited since approval', tone: 'draft' }],
        dependsOn: ['demo'],
        helpfulPrerequisites: [],
      },
      third: {
        learnerOutcome: 'Use the skill.',
        cefrLevel: 'A2',
        findings: [],
        statusChips: [{ label: 'findings 2', tone: 'warning' }],
        dependsOn: ['second'],
        helpfulPrerequisites: [],
      },
    },
    chat: [
      {
        ...chatTurnFixture({ role: 'assistant', text: 'Curriculum chat', kind: 'curriculum' }),
      },
    ],
    findings: [],
  };
}

export function workbenchSettingsPayload(): WorkbenchSettingsResponse {
  const roleSetting = { model: 'openai/gpt-6.1-sol', effort: 'medium' as const };
  const openCodeModels = [
    'openai/gpt-6.1-sol',
    'openai/gpt-6-luna',
    'openai/gpt-6-astra',
    'zai-coding-plan/glm-5.3',
    'opencode-go/glm-5.3',
    'openai/gpt-5.6-luna',
    'openai/gpt-5.6-sol',
    'anthropic/claude-sonnet-5-5',
  ];
  const openCodeEfforts = ['low', 'medium', 'high', 'xhigh'] as const;
  return {
    settings: {
      roles: {
        chat: { model: 'gpt-6.1-sol', effort: 'medium' },
        generation: roleSetting,
        lesson_review: roleSetting,
        exercise_review: roleSetting,
      },
      second_opinion: { enabled: false, model: 'anthropic/claude-sonnet-5-5' },
      defaults: { review_scope: 'lesson' },
      audio: { monthly_cap_usd: 5 },
    },
    modelOptions: {
      chat: ['gpt-6.1-sol', 'gpt-6-luna', 'gpt-6-astra', 'gpt-5.6-luna', 'gpt-5.6-sol'],
      generation: openCodeModels,
      lesson_review: openCodeModels,
      exercise_review: openCodeModels,
      second_opinion: openCodeModels,
    },
    effortOptions: {
      chat: ['minimal', ...openCodeEfforts],
      generation: [...openCodeEfforts],
      lesson_review: [...openCodeEfforts],
      exercise_review: [...openCodeEfforts],
    },
    reviewScopeOptions: ['lesson', 'section', 'item'],
  };
}

export function renderAt(
  path: string,
  data: { lesson?: LessonPayload; curriculum?: CurriculumPayload } = {},
): ReturnType<typeof render> {
  window.history.pushState(null, '', path);
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  client.setQueryData(['curriculum'], data.curriculum ?? curriculumPayload());
  client.setQueryData(['curriculum', 'checks'], { counts: {} });
  client.setQueryData(['settings'], workbenchSettingsPayload());
  if (data.lesson) client.setQueryData(['lesson', data.lesson.lessonId], data.lesson);
  return render(<App queryClient={client} lessonPageComponent={LessonPage} />);
}

export function renderAtCold(path: string): ReturnType<typeof render> {
  window.history.pushState(null, '', path);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0 } } });
  return render(<App queryClient={client} lessonPageComponent={LessonPage} />);
}
