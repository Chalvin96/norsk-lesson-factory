import { LoaderCircleIcon } from 'lucide-react';
import * as React from 'react';
import type { CurriculumLesson } from '@/api/contracts';
import { Button } from '@/components/ui/button';
import type { NewLessonJob } from '@/jobs/new-lesson-jobs';

const NewLessonDialog = React.lazy(() =>
  import('./dialogs/new-lesson-dialog').then(({ NewLessonDialog }) => ({ default: NewLessonDialog })),
);

export function NewLessonSession({
  lessons,
  state: newLesson,
  selectedId,
  onComplete,
}: {
  lessons: CurriculumLesson[];
  state: NewLessonJob;
  selectedId: string | undefined;
  onComplete: (slug: string) => void;
}) {
  const [newLessonOpen, setNewLessonOpen] = React.useState(!newLesson.request);
  const completedSlug = newLesson.job.isSuccess ? newLesson.request?.slug : undefined;
  const notifiedSlug = React.useRef<string | undefined>(undefined);
  React.useEffect(() => {
    if (completedSlug) {
      if (notifiedSlug.current !== completedSlug) {
        notifiedSlug.current = completedSlug;
        onComplete(completedSlug);
      }
      if (!newLessonOpen) newLesson.close();
    }
  }, [completedSlug, onComplete, newLessonOpen, newLesson.close]);
  return (
    <>
      {!newLessonOpen && newLesson.request && (newLesson.job.isPending || newLesson.job.isError) ? (
        <div
          role={newLesson.job.isError ? 'alert' : 'status'}
          className={`flex min-w-0 max-w-full items-center gap-2 text-xs ${newLesson.job.isError ? 'sm:max-w-80' : 'sm:max-w-52'}`}
        >
          {newLesson.job.isPending ? (
            <LoaderCircleIcon aria-label="Drafting" className="size-4 shrink-0 animate-spin" />
          ) : null}
          <span
            className="min-w-0 truncate"
            title={`${newLesson.request.title}: ${newLesson.job.isError ? newLesson.job.error.message : (newLesson.job.steps.at(-1) ?? 'Waiting to start…')}`}
          >
            {newLesson.request.title}:{' '}
            {newLesson.job.isError ? newLesson.job.error.message : (newLesson.job.steps.at(-1) ?? 'Waiting to start…')}
          </span>
          {newLesson.job.isError ? (
            <>
              <Button size="sm" className="shrink-0" variant="outline" onClick={newLesson.job.retry}>
                Retry
              </Button>
              <Button
                size="sm"
                className="shrink-0"
                variant="outline"
                onClick={() => {
                  newLesson.setRequest(null);
                  setNewLessonOpen(true);
                }}
              >
                Edit inputs
              </Button>
              <Button size="sm" className="shrink-0" variant="outline" onClick={newLesson.dismiss}>
                Dismiss
              </Button>
            </>
          ) : (
            <Button size="sm" className="shrink-0" variant="outline" onClick={() => setNewLessonOpen(true)}>
              View job
            </Button>
          )}
        </div>
      ) : null}
      <React.Suspense
        fallback={
          <p role="status" className="sr-only">
            Loading new lesson form…
          </p>
        }
      >
        <NewLessonDialog
          open={newLessonOpen}
          state={newLesson}
          lessons={lessons}
          selectedId={selectedId}
          onOpenChange={(open) => {
            setNewLessonOpen(open);
            if (!open) newLesson.close();
          }}
        />
      </React.Suspense>
    </>
  );
}
