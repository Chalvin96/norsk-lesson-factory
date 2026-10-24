/** Entry point: `useLessonReviewWorkflow`. */

import * as React from 'react';
import { toast } from 'sonner';

import type { ReviewScope } from '@/api/contracts';

interface ReviewRow {
  rowId: string;
  targetKey: string;
}

type ReviewRequest =
  | { route: string; body: Record<string, never> }
  | { route: string; body: { scope: 'lesson' } }
  | { route: string; body: { scope: 'section' | 'item'; key: string } };

export interface LessonReviewWorkflowOptions {
  lessonId: string;
  rows: ReviewRow[];
  selectedRowId: string | null;
  currentSectionKey: () => string | null | undefined;
  defaultReviewScope?: ReviewScope;
  mutate: (request: ReviewRequest) => void;
}

export function useLessonReviewWorkflow({
  lessonId,
  rows,
  selectedRowId,
  currentSectionKey,
  defaultReviewScope,
  mutate,
}: LessonReviewWorkflowOptions) {
  const [reviewScopeOverride, setReviewScopeOverride] = React.useState<ReviewScope | null>(null);
  const reviewScope = reviewScopeOverride ?? defaultReviewScope ?? 'lesson';

  const runReview = React.useCallback(
    (requestedScope?: ReviewScope) => {
      const scope = requestedScope ?? reviewScope;
      const item = rows.find((row) => row.rowId === selectedRowId);
      const key = scope === 'section' ? currentSectionKey()?.split('#')[0] : item?.targetKey;
      if (scope !== 'lesson' && !key) {
        toast.error(scope === 'section' ? 'Scroll to a lesson section first.' : 'Focus or open a lesson row first.');
        return;
      }
      if (requestedScope) setReviewScopeOverride(scope);
      const route = `/api/lessons/${encodeURIComponent(lessonId)}/review`;
      if (scope === 'lesson') mutate({ route, body: { scope } });
      else if (key) mutate({ route, body: { scope, key } });
    },
    [currentSectionKey, lessonId, mutate, reviewScope, rows, selectedRowId],
  );

  const runReviewUncovered = React.useCallback(
    () => mutate({ route: `/api/lessons/${encodeURIComponent(lessonId)}/review/uncovered`, body: {} }),
    [lessonId, mutate],
  );

  return { reviewScope, runReview, runReviewUncovered };
}
