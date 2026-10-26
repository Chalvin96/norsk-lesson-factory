/** Entry points: curriculum queries and writes used by curriculum pages and dialogs. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { CurriculumChecks, CurriculumEditPreview, CurriculumPayload, CurriculumSaveResult } from '@/api/contracts';
import { getJson, writeJson } from '@/api/http';
import { hasActiveChatTurn } from '@/jobs/use-workbench-job';
import type { CurriculumEdit, CurriculumSaveInput } from './types';

export function useCurriculum() {
  return useQuery({
    queryKey: ['curriculum'],
    queryFn: () => getJson<CurriculumPayload>('/api/curriculum'),
    staleTime: 30_000,
    refetchInterval: (query) => (hasActiveChatTurn(query.state.data?.chat) ? 1000 : false),
  });
}
/** Check counts compile every lesson, so rows load first and counts fill in afterwards. */
export function useCurriculumChecks() {
  return useQuery({
    queryKey: ['curriculum', 'checks'],
    queryFn: () => getJson<CurriculumChecks>('/api/curriculum/checks'),
    staleTime: 30_000,
  });
}
export function useSaveCurriculum() {
  const client = useQueryClient();
  return useMutation({
    scope: { id: 'curriculum-write' },
    mutationFn: (input: CurriculumSaveInput) =>
      writeJson<CurriculumSaveResult>('/api/curriculum/save', 'PUT', {
        edits: input.edits.map(toCurriculumEditRequest),
        review_digest: input.reviewDigest,
      }),
    onSuccess: (result) => client.setQueryData(['curriculum'], result.curriculum),
  });
}
export function usePreviewCurriculumEdits() {
  return useMutation({
    mutationFn: (edits: CurriculumEdit[]) =>
      writeJson<CurriculumEditPreview>('/api/curriculum/preview', 'POST', {
        edits: edits.map(toCurriculumEditRequest),
      }),
  });
}
function toCurriculumEditRequest(edit: CurriculumEdit): object {
  switch (edit.kind) {
    case 'reorder':
      return { kind: edit.kind, lesson_ids: edit.lessonIds };
    case 'add':
      return {
        kind: edit.kind,
        entry: {
          catalog_id: edit.entry.catalogId,
          title: edit.entry.title,
          learner_outcome: edit.entry.learnerOutcome,
          cefr_level: edit.entry.cefrLevel,
          prerequisites: edit.entry.prerequisites,
          helpful_prerequisites: edit.entry.helpfulPrerequisites,
        },
        after_lesson_id: edit.afterLessonId,
      };
    case 'remove':
      return {
        kind: edit.kind,
        lesson_ids: edit.lessonIds,
        delete_never_promoted_drafts: edit.deleteNeverPromotedDrafts,
      };
    case 'merge':
      return { kind: edit.kind, lesson_ids: edit.lessonIds, title: edit.title, learner_outcome: edit.learnerOutcome };
    case 'split':
      return {
        kind: edit.kind,
        lesson_id: edit.lessonId,
        parts: edit.parts.map((part) => ({
          catalog_id: part.catalogId,
          title: part.title,
          learner_outcome: part.learnerOutcome,
        })),
      };
    case 'update':
      return {
        kind: edit.kind,
        lesson_id: edit.lessonId,
        title: edit.title,
        learner_outcome: edit.learnerOutcome,
        cefr_level: edit.cefrLevel,
        prerequisites: edit.prerequisites,
        helpful_prerequisites: edit.helpfulPrerequisites,
      };
    default:
      return assertNever(edit);
  }
}
function assertNever(value: never): never {
  throw new Error(`Unsupported curriculum edit: ${JSON.stringify(value)}`);
}
