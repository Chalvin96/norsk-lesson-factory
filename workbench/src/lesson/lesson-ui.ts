/** Entry points: `parseLessonRouteState` validates URL state; `lessonReducer` updates local lesson UI state. */

export type Mode = 'read' | 'review';
export type Stage = 'lesson' | 'plan';
export type View = 'author' | 'learner' | 'source';

export interface LessonRouteState {
  mode: Mode;
  stage: Stage;
  view: View;
}

export interface LessonLocalState {
  currentFindingId: string | null;
  openRowId: string | null;
  editedRowIds: string[];
}

export type LessonUi = LessonRouteState & LessonLocalState;

export type LessonAction =
  | { type: 'focus-finding'; findingId: string }
  | { type: 'open-row'; rowId: string | null }
  | { type: 'mark-edited'; rowId: string };

const K_LESSON_UI_MODES = ['read', 'review'] as const satisfies readonly Mode[];
const K_LESSON_UI_STAGES = ['lesson', 'plan'] as const satisfies readonly Stage[];
const K_LESSON_UI_VIEWS = ['author', 'learner', 'source'] as const satisfies readonly View[];

export function parseLessonRouteState(search: string): LessonRouteState {
  const params = new URLSearchParams(search);
  return {
    mode: K_LESSON_UI_MODES.find((mode) => mode === params.get('mode')) ?? 'review',
    stage: K_LESSON_UI_STAGES.find((stage) => stage === params.get('stage')) ?? 'lesson',
    view: K_LESSON_UI_VIEWS.find((view) => view === params.get('view')) ?? 'author',
  };
}

export function writeLessonRouteState(params: URLSearchParams, state: LessonRouteState): URLSearchParams {
  const next = new URLSearchParams(params);
  if (state.mode === 'review') next.delete('mode');
  else next.set('mode', state.mode);
  if (state.stage === 'lesson') next.delete('stage');
  else next.set('stage', state.stage);
  if (state.view === 'author') next.delete('view');
  else next.set('view', state.view);
  return next;
}

export function buildInitialLessonLocalState(): LessonLocalState {
  return {
    currentFindingId: null,
    openRowId: null,
    editedRowIds: [],
  };
}

export function lessonReducer(ui: LessonLocalState, action: LessonAction): LessonLocalState {
  switch (action.type) {
    case 'focus-finding':
      return { ...ui, currentFindingId: action.findingId };
    case 'open-row':
      return { ...ui, openRowId: action.rowId };
    case 'mark-edited':
      return {
        ...ui,
        editedRowIds: ui.editedRowIds.includes(action.rowId) ? ui.editedRowIds : [...ui.editedRowIds, action.rowId],
      };
  }
}
