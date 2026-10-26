/** Not a check itself — curriculum edit and draft input types owned by curriculum workflows. */

import type { WorkbenchWire } from '@/api/contracts';

type ApiCatalog = WorkbenchWire.WorkbenchApiSchemaCatalog;
type LessonStateWire = NonNullable<ApiCatalog['responses']['lesson']>['state'];
type StoredChatTurnWire = LessonStateWire['chat'][number];

/** Frontend edit shape converted to the Python snake_case request by the curriculum API. */
export type CurriculumEdit = NonNullable<StoredChatTurnWire['proposedEdit']>;

export interface CurriculumSaveInput {
  edits: CurriculumEdit[];
  reviewDigest: string | null;
}

export type EntryEdit = Pick<
  NonNullable<ApiCatalog['requests']['curriculum.lesson.create']>,
  'title' | 'learner_outcome' | 'cefr_level'
>;
