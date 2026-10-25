import type { Meta, StoryObj } from '@storybook/react-vite';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type * as React from 'react';
import { expect, userEvent, within } from 'storybook/test';
import type { CurriculumPayload, LessonPayload } from '@/api/contracts';
import { ApiError } from '@/api/http';
import { AppShell } from '@/app/app-shell';
import { Toaster } from '@/components/ui/sonner';
import { CurriculumPage } from '@/curriculum/curriculum-page';
import { NewLessonJobsProvider } from '@/jobs/new-lesson-jobs';
import { LessonPage } from '@/lesson/lesson-page';
import { SettingsPage } from '@/settings/settings-page';
import { curriculumPayload, lessonPayload, workbenchSettingsPayload } from '@/test/fixtures';

function Page({
  lesson,
  curriculum,
  curriculumFails,
  lessonFails,
  children,
}: {
  lesson?: LessonPayload;
  curriculum?: CurriculumPayload;
  curriculumFails?: boolean;
  lessonFails?: boolean;
  children: React.ReactNode;
}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  if (!curriculumFails) client.setQueryData(['curriculum'], curriculum ?? curriculumPayload());
  else client.setQueryDefaults(['curriculum'], { queryFn: () => Promise.reject(new Error('plan unreadable')) });
  if (lesson && !lessonFails) client.setQueryData(['lesson', lesson.lessonId], lesson);
  if (lesson && lessonFails)
    client.setQueryDefaults(['lesson', lesson.lessonId], {
      queryFn: () => Promise.reject(new ApiError(503, 'rebuild_failed')),
    });
  client.setQueryData(['settings'], workbenchSettingsPayload());
  return (
    <QueryClientProvider client={client}>
      <NewLessonJobsProvider>
        {children}
        <Toaster />
      </NewLessonJobsProvider>
    </QueryClientProvider>
  );
}

function LessonStory({
  lesson,
  curriculumFails,
  lessonFails,
}: {
  lesson: LessonPayload;
  curriculumFails?: boolean;
  lessonFails?: boolean;
}) {
  return (
    <Page lesson={lesson} curriculumFails={curriculumFails} lessonFails={lessonFails}>
      <AppShell
        route={{ page: 'lesson', lessonId: lesson.lessonId, ui: { mode: 'review', stage: 'lesson', view: 'author' } }}
      >
        <LessonPage lessonId={lesson.lessonId} routeState={{ mode: 'review', stage: 'lesson', view: 'author' }} />
      </AppShell>
    </Page>
  );
}

const meta = {
  title: 'Pages/Workbench',
  component: LessonStory,
  parameters: { layout: 'fullscreen' },
  args: { lesson: lessonPayload() },
} satisfies Meta<typeof LessonStory>;
export default meta;
type Story = StoryObj<typeof meta>;

export const LessonReview: Story = {};
const choose =
  (label: string): Story['play'] =>
  async ({ canvasElement }) => {
    await userEvent.click(await within(canvasElement).findByRole('radio', { name: label }));
  };

export const LessonRead: Story = { play: choose('Read') };
export const LessonSource: Story = { play: choose('Source') };
export const LessonLearner: Story = { play: choose('Learner') };
export const LessonLearnerWithGloss: Story = {
  play: async ({ canvasElement }) => {
    await userEvent.click(await within(canvasElement).findByRole('radio', { name: 'Learner' }));
    await userEvent.click(await within(canvasElement).findByRole('button', { name: /English gloss/ }));
  },
};
export const LessonIncorrectExample: Story = {
  args: {
    lesson: lessonPayload({
      elements: [
        {
          kind: 'section',
          id: 'examples',
          title: 'Example validity',
          role: 'model',
          blocks: [
            {
              kind: 'examples',
              items: [
                {
                  teaching_role: 'incorrect',
                  no: [{ kind: 'text', value: 'Gi meg en kaffe.' }],
                  en: [{ kind: 'text', value: 'Give me a coffee.' }],
                },
                {
                  teaching_role: 'model',
                  no: [{ kind: 'text', value: 'Kan jeg få en kaffe?' }],
                  en: [{ kind: 'text', value: 'Can I have a coffee?' }],
                },
              ],
            },
          ],
        },
      ],
    }),
  },
};
export const LessonPlan: Story = { play: choose('Plan') };
export const LessonRebuildError: Story = { args: { lessonFails: true } };
export const LessonDraftReadyToApprove: Story = {
  args: {
    lesson: lessonPayload({
      source: 'draft',
      sourceLabel: 'drafts/demo',
      draft: { exists: true, startedFrom: 'canonical-digest', dirty: true, lastCommit: null },
      state: {
        ...lessonPayload().state,
        findings: [],
        proposals: [],
        promotionBlockers: [],
        statusChips: [{ label: 'edited since approval', tone: 'draft' }],
      },
    }),
  },
};
export const LessonClientEditBlocksApproval: Story = {
  args: {
    lesson: lessonPayload({ state: { ...lessonPayload().state, findings: [], proposals: [], promotionBlockers: [] } }),
  },
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    await userEvent.click(await canvas.findByRole('button', { name: 'Edit block' }));
    await userEvent.clear(canvas.getByLabelText('Translation'));
    await userEvent.type(canvas.getByLabelText('Translation'), 'Updated translation');
    await canvas.findByText('Save changes first');
  },
};
export const LessonAudioStates: Story = {
  args: {
    lesson: (() => {
      const lesson = lessonPayload();
      const ready = lesson.audio.clips[0];
      return lessonPayload({
        audio: {
          ...lesson.audio,
          clips: [
            ready,
            { ...ready, binding: 'section:intro:block:1', status: 'missing', text: 'Hva heter du?', durationMs: null },
            {
              ...ready,
              binding: 'section:intro:block:2',
              status: 'stale',
              text: 'Hyggelig å møte deg.',
              findings: [{ code: 'length', message: 'Clip duration is outside the expected range.' }],
            },
          ],
          missingCount: 1,
          staleCount: 1,
          readyCount: 1,
          voices: [{ ...lesson.audio.voices[0], changedSinceRelease: true, releasedVoice: 'nb-NO-Leda' }],
          blockers: ['1 stale clip', '1 open audio check'],
        },
      });
    })(),
  },
};
export const LessonCompileError: Story = {
  args: {
    lesson: lessonPayload({ elements: [], errors: ['compile preview: exercises.yaml is malformed at line 12'] }),
  },
};
export const LessonListUnavailable: Story = { args: { curriculumFails: true } };
export const Curriculum: Story = {
  render: () => (
    <Page>
      <AppShell route={{ page: 'curriculum' }}>
        <CurriculumPage />
      </AppShell>
    </Page>
  ),
};
export const CurriculumNarrow: Story = {
  globals: { viewport: { value: 'mobile320', isRotated: false } },
  render: () => {
    const curriculum = curriculumPayload();
    const titles = [
      'Hilse og presentere seg — greetings and introductions',
      'Be om gjentakelse eller forklaring — ask for repetition or clarification',
      'Stille enkle spørsmål i en samtale',
    ];
    return (
      <Page
        curriculum={{
          ...curriculum,
          lessons: curriculum.lessons.map((lesson, index) => ({
            ...lesson,
            title: titles[index] ?? lesson.title,
            source: index === 2 ? 'missing' : lesson.source,
          })),
        }}
      >
        <AppShell route={{ page: 'curriculum' }}>
          <CurriculumPage />
        </AppShell>
      </Page>
    );
  },
};
const curriculumEditPreview = (
  entries: {
    sequenceIndex: number;
    catalogId: string;
    title: string;
    learnerOutcome: string;
    cefrLevel: string;
    prerequisites: string[];
    helpfulPrerequisites: string[];
  }[],
  affectedLessons: {
    lessonId: string;
    title: string;
    effect: 'needs_replan' | 'unlisted' | 'new_entry' | 'order_changed';
    detail: string;
  }[] = [],
) => ({
  reviewDigest: 'storybook-review',
  entries,
  affectedLessons,
  movedOutcomes: [],
  movedPrerequisites: [],
  dependencyFindings: [],
});
function stubCurriculumPreview(canvasElement: HTMLElement, result: object) {
  const view = canvasElement.ownerDocument.defaultView;
  if (!view) throw new Error('Storybook window is unavailable.');
  view.fetch = async () => new Response(JSON.stringify(result), { headers: { 'Content-Type': 'application/json' } });
}
function stubCurriculumPreviews(canvasElement: HTMLElement, results: object[]) {
  const view = canvasElement.ownerDocument.defaultView;
  if (!view) throw new Error('Storybook window is unavailable.');
  let index = 0;
  view.fetch = async () =>
    new Response(JSON.stringify(results[Math.min(index++, results.length - 1)]), {
      headers: { 'Content-Type': 'application/json' },
    });
}
export const CurriculumMergePreview: Story = {
  ...Curriculum,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    stubCurriculumPreview(
      canvasElement,
      curriculumEditPreview(
        [
          {
            sequenceIndex: 0,
            catalogId: 'demo',
            title: 'Help in conversation',
            learnerOutcome: 'Ask for help, repeat, or clarify.',
            cefrLevel: 'A1',
            prerequisites: [],
            helpfulPrerequisites: [],
          },
          {
            sequenceIndex: 1,
            catalogId: 'third',
            title: 'Question forms',
            learnerOutcome: 'Ask a short question.',
            cefrLevel: 'A2',
            prerequisites: [],
            helpfulPrerequisites: [],
          },
        ],
        [
          {
            lessonId: 'demo',
            title: 'Help in conversation',
            effect: 'needs_replan',
            detail: 'The merged lesson needs re-planning.',
          },
        ],
      ),
    );
    await userEvent.click(canvas.getByRole('checkbox', { name: 'Select Demo lesson' }));
    await userEvent.click(canvas.getByRole('checkbox', { name: 'Select Second lesson' }));
    await userEvent.click(canvas.getByRole('button', { name: 'Merge…' }));
    await canvas.findByRole('heading', { name: 'Merge entries' });
  },
};
export const CurriculumSplitPreview: Story = {
  ...Curriculum,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    stubCurriculumPreview(
      canvasElement,
      curriculumEditPreview([
        {
          sequenceIndex: 0,
          catalogId: 'demo',
          title: 'Greetings',
          learnerOutcome: 'Greet someone.',
          cefrLevel: 'A1',
          prerequisites: [],
          helpfulPrerequisites: [],
        },
        {
          sequenceIndex: 1,
          catalogId: 'introduce_yourself',
          title: 'Introductions',
          learnerOutcome: 'Introduce yourself.',
          cefrLevel: 'A1',
          prerequisites: ['demo'],
          helpfulPrerequisites: [],
        },
        {
          sequenceIndex: 2,
          catalogId: 'second',
          title: 'Second lesson',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A1',
          prerequisites: ['introduce_yourself'],
          helpfulPrerequisites: [],
        },
      ]),
    );
    await userEvent.click(canvas.getByRole('button', { name: 'More actions for Demo lesson' }));
    await userEvent.click(await canvas.findByRole('menuitem', { name: 'Split entry' }));
    await userEvent.clear(canvas.getByLabelText('Part 2 title'));
    await userEvent.type(canvas.getByLabelText('Part 2 title'), 'Introductions');
    await userEvent.type(canvas.getByLabelText('Part 2 learner outcome'), 'Introduce yourself.');
    await canvas.findByRole('button', { name: 'Accept split' });
  },
};
export const CurriculumRemovePreview: Story = {
  ...Curriculum,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    stubCurriculumPreview(
      canvasElement,
      curriculumEditPreview(
        [
          {
            sequenceIndex: 0,
            catalogId: 'second',
            title: 'Second lesson',
            learnerOutcome: 'Use the skill.',
            cefrLevel: 'A1',
            prerequisites: [],
            helpfulPrerequisites: [],
          },
          {
            sequenceIndex: 1,
            catalogId: 'third',
            title: 'Third lesson',
            learnerOutcome: 'Use the skill.',
            cefrLevel: 'A2',
            prerequisites: ['second'],
            helpfulPrerequisites: [],
          },
        ],
        [
          {
            lessonId: 'demo',
            title: 'Demo lesson',
            effect: 'unlisted',
            detail: 'The lesson package stays in the workspace.',
          },
        ],
      ),
    );
    await userEvent.click(canvas.getByRole('checkbox', { name: 'Select Demo lesson' }));
    await userEvent.click(canvas.getByRole('button', { name: 'Remove…' }));
    await canvas.findByRole('button', { name: 'Accept removal' });
  },
};
export const CurriculumStagedEditPreview: Story = {
  ...Curriculum,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    stubCurriculumPreviews(canvasElement, [
      curriculumEditPreview(
        [
          {
            sequenceIndex: 0,
            catalogId: 'demo',
            title: 'Merged entries',
            learnerOutcome: 'Use the skill.',
            cefrLevel: 'A1',
            prerequisites: [],
            helpfulPrerequisites: [],
          },
          {
            sequenceIndex: 1,
            catalogId: 'third',
            title: 'Third lesson',
            learnerOutcome: 'Use the skill.',
            cefrLevel: 'A2',
            prerequisites: ['demo'],
            helpfulPrerequisites: [],
          },
        ],
        [
          {
            lessonId: 'demo',
            title: 'Merged entries',
            effect: 'needs_replan',
            detail: 'The staged merge changes this lesson.',
          },
        ],
      ),
      curriculumEditPreview(
        [
          {
            sequenceIndex: 0,
            catalogId: 'second',
            title: 'Second lesson',
            learnerOutcome: 'Use the skill.',
            cefrLevel: 'A1',
            prerequisites: ['demo'],
            helpfulPrerequisites: [],
          },
          {
            sequenceIndex: 1,
            catalogId: 'third',
            title: 'Third lesson',
            learnerOutcome: 'Use the skill.',
            cefrLevel: 'A2',
            prerequisites: ['second'],
            helpfulPrerequisites: [],
          },
        ],
        [{ lessonId: 'demo', title: 'Demo lesson', effect: 'unlisted', detail: 'This edit removes Demo lesson.' }],
      ),
      curriculumEditPreview(
        [
          {
            sequenceIndex: 0,
            catalogId: 'third',
            title: 'Third lesson',
            learnerOutcome: 'Use the skill.',
            cefrLevel: 'A2',
            prerequisites: [],
            helpfulPrerequisites: [],
          },
        ],
        [
          {
            lessonId: 'third',
            title: 'Third lesson',
            effect: 'needs_replan',
            detail: 'Cumulative impact after the staged merge and this removal.',
          },
        ],
      ),
    ]);
    await userEvent.click(canvas.getByRole('checkbox', { name: 'Select Demo lesson' }));
    await userEvent.click(canvas.getByRole('checkbox', { name: 'Select Second lesson' }));
    await userEvent.click(canvas.getByRole('button', { name: 'Merge…' }));
    const acceptMerge = await canvas.findByRole('button', { name: 'Accept merge' });
    await expect(acceptMerge).toBeEnabled();
    await userEvent.click(acceptMerge);
    await userEvent.click(canvas.getByRole('checkbox', { name: 'Select Merged entries' }));
    await userEvent.click(canvas.getByRole('button', { name: 'Remove…' }));
    await canvas.findByText('This edit removes Demo lesson.');
    await canvas.findByText('All staged edits (2)');
  },
};
export const CurriculumReorderReview: Story = {
  ...Curriculum,
  play: async ({ canvasElement }) => {
    const canvas = within(canvasElement);
    stubCurriculumPreview(
      canvasElement,
      curriculumEditPreview([
        {
          sequenceIndex: 0,
          catalogId: 'demo',
          title: 'Demo lesson',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A1',
          prerequisites: [],
          helpfulPrerequisites: [],
        },
        {
          sequenceIndex: 1,
          catalogId: 'third',
          title: 'Third lesson',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A2',
          prerequisites: [],
          helpfulPrerequisites: [],
        },
        {
          sequenceIndex: 2,
          catalogId: 'second',
          title: 'Second lesson',
          learnerOutcome: 'Use the skill.',
          cefrLevel: 'A1',
          prerequisites: ['demo'],
          helpfulPrerequisites: [],
        },
      ]),
    );
    const third = canvas.getByRole('button', { name: 'Open Third lesson details' });
    third.focus();
    await userEvent.keyboard('{Alt>}{ArrowUp}{/Alt}');
    await userEvent.click(canvas.getByRole('button', { name: 'Save curriculum' }));
    await canvas.findByRole('heading', { name: 'Save curriculum changes' });
  },
};
export const Settings: Story = {
  render: () => (
    <Page>
      <AppShell route={{ page: 'settings' }}>
        <SettingsPage />
      </AppShell>
    </Page>
  ),
};
