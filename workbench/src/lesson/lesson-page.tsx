/** Entry point: `LessonPage`. */

import * as React from 'react';
import { toast } from 'sonner';

import { ApiError } from '@/api/http';
import { PageStatus } from '@/components/workbench/page-status';
import { useLesson } from '@/lesson/api';
import { LessonWorkspace } from '@/lesson/lesson-workspace';
import { useWorkbenchSettings } from '@/settings/api';

import type { LessonRouteState } from './lesson-ui';

export function LessonPage({ lessonId, routeState }: { lessonId: string; routeState: LessonRouteState }) {
  const settings = useWorkbenchSettings();
  const lesson = useLesson(lessonId);
  React.useEffect(() => {
    if (lesson.isError && lesson.data)
      toast.error('Refresh failed. Your editor text is still here; retry by reloading after writing your draft.');
  }, [lesson.data, lesson.isError]);
  if (lesson.data)
    return (
      <LessonWorkspace
        key={lessonId}
        lesson={lesson.data}
        routeState={routeState}
        defaultReviewScope={settings.data?.settings.defaults.review_scope}
      />
    );
  if (lesson.isError) {
    const notFound = lesson.error instanceof ApiError && lesson.error.status === 404;
    return (
      <PageStatus tone="error">
        {notFound
          ? 'Lesson not found.'
          : 'Workbench unavailable: the source could not be rebuilt. Fix the authored source and reload.'}
      </PageStatus>
    );
  }
  return <PageStatus>Loading lesson…</PageStatus>;
}
