import { CheckIcon, LoaderCircleIcon, XIcon } from 'lucide-react';
import * as React from 'react';
import type { CurriculumLesson } from '@/api/contracts';
import { lessonHref, navigate } from '@/app/router';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import type { EntryEdit } from '@/curriculum/types';
import { JobErrorDisclosure } from '@/jobs/job-error-disclosure';
import type { NewLessonJob } from '@/jobs/new-lesson-jobs';
import { WorkbenchJobError } from '@/jobs/use-workbench-job';

import { EntryFields } from '../entry-form';
import { deriveSlug } from '../form-values';

export function NewLessonDialog({
  open,
  lessons,
  selectedId,
  onOpenChange,
  state,
}: {
  state: NewLessonJob;
  open: boolean;
  lessons: CurriculumLesson[];
  selectedId: string | undefined;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <NewLessonContent lessons={lessons} selectedId={selectedId} state={state} onClose={() => onOpenChange(false)} />
    </Dialog>
  );
}

function NewLessonContent({
  lessons,
  selectedId,
  onClose,
  state,
}: {
  lessons: CurriculumLesson[];
  selectedId: string | undefined;
  onClose: () => void;
  state: NewLessonJob;
}) {
  const [value, setValue] = React.useState<EntryEdit>(
    state.inputs ?? { title: '', learner_outcome: '', cefr_level: 'A1' },
  );
  const [slug, setSlug] = React.useState(state.inputs?.slug ?? '');
  const [slugEdited, setSlugEdited] = React.useState(Boolean(state.inputs));
  const [after, setAfter] = React.useState(
    state.inputs ? (state.inputs.after_lesson_id ?? 'beginning') : (selectedId ?? 'beginning'),
  );
  const { request, setRequest, job } = state;
  const validSlug = /^[a-z0-9]+(?:_[a-z0-9]+)*$/.test(slug);
  const duplicate = lessons.some((lesson) => lesson.lessonId === slug);
  function findInvalidField() {
    if (!value.title.trim()) return { id: 'new-title', reason: 'Fill in the title to continue.' };
    if (!value.learner_outcome.trim()) return { id: 'new-outcome', reason: 'Fill in the learner outcome to continue.' };
    if (!validSlug) return { id: 'new-slug', reason: 'Use a valid slug to continue.' };
    if (duplicate) return { id: 'new-slug', reason: 'Choose an unused slug to continue.' };
    return null;
  }
  const invalidField = findInvalidField();
  const valid = invalidField === null;
  const disabledReason = invalidField?.reason;
  const activeStep = job.steps.at(-1);
  return (
    <DialogContent
      className="max-h-[90vh] grid-cols-1 overflow-y-auto"
      onInteractOutside={(event) => {
        if (job.isPending) event.preventDefault();
      }}
      onEscapeKeyDown={(event) => {
        if (job.isPending) event.preventDefault();
      }}
    >
      <DialogHeader className="text-left">
        <DialogTitle>New lesson</DialogTitle>
        <DialogDescription>
          {request
            ? 'Drafting a complete lesson package. You can close this dialog; the job continues on the server.'
            : 'Add a curriculum entry and draft its plan, lesson and exercises.'}
        </DialogDescription>
      </DialogHeader>
      {request ? (
        <div className="grid min-w-0 grid-cols-1 gap-4">
          <p className="font-semibold">{request.title}</p>
          <ol aria-label="Drafting progress" aria-live="polite" className="grid gap-2 text-sm">
            {job.steps.map((step, index) => (
              <li key={index} className="flex items-center gap-2">
                {job.isPending && index === job.steps.length - 1 ? (
                  <LoaderCircleIcon aria-hidden className="size-4 animate-spin" />
                ) : job.isError && index === job.steps.length - 1 ? (
                  <XIcon aria-hidden className="size-4 text-destructive-90" />
                ) : (
                  <CheckIcon aria-hidden className="size-4" />
                )}
                <span>{step}</span>
              </li>
            ))}
            {job.isPending && !activeStep ? (
              <li role="status" className="flex items-center gap-2">
                <LoaderCircleIcon aria-hidden className="size-4 animate-spin" />
                Waiting to start…
              </li>
            ) : null}
          </ol>
          {job.isError ? (
            <div role="alert" className="min-w-0 text-sm break-words text-destructive-90">
              <p>{job.error.message}</p>
              <JobErrorDisclosure
                detail={
                  (job.error instanceof WorkbenchJobError ? job.error.detail : null) ||
                  job.checkErrors.join('\n') ||
                  null
                }
              />
              <div className="mt-3 flex gap-2">
                <Button variant="outline" onClick={job.retry}>
                  Retry
                </Button>
                <Button variant="outline" onClick={() => setRequest(null)}>
                  Edit inputs
                </Button>
              </div>
            </div>
          ) : null}
          {job.isSuccess ? (
            <div role="status" className="grid gap-2 text-sm">
              <p>Draft check passed.</p>
              {job.findings.length ? (
                <ul className="list-disc space-y-1 pl-5">
                  {job.findings.map((finding, index) => (
                    <li key={index}>{finding}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-muted-foreground">No check findings.</p>
              )}
            </div>
          ) : null}
          {job.isSuccess ? (
            <Button
              onClick={() => {
                onClose();
                navigate(lessonHref(request.slug));
              }}
            >
              Open lesson
            </Button>
          ) : null}
        </div>
      ) : (
        <form
          noValidate
          className="grid gap-4"
          onSubmit={(event) => {
            event.preventDefault();
            if (invalidField) {
              event.currentTarget.querySelector<HTMLElement>(`#${invalidField.id}`)?.focus();
              return;
            }
            const created = { ...value, slug, after_lesson_id: after === 'beginning' ? null : after };
            setRequest(created);
            job.mutate({ route: '/api/curriculum/lessons', body: created });
          }}
        >
          <EntryFields
            prefix="new"
            value={value}
            onChange={(next) => {
              setValue(next);
              if (!slugEdited) setSlug(deriveSlug(next.title));
            }}
          />
          <div className="grid gap-1.5">
            <Label htmlFor="new-slug">Slug</Label>
            <Input
              id="new-slug"
              required
              value={slug}
              aria-describedby={invalidField?.id === 'new-slug' ? 'new-lesson-disabled-reason' : 'slug-help'}
              aria-invalid={Boolean(slug) && (!validSlug || duplicate)}
              onChange={(event) => {
                setSlugEdited(true);
                setSlug(event.target.value);
              }}
            />
            <p
              hidden={invalidField?.id === 'new-slug'}
              id="slug-help"
              className={`text-xs ${duplicate || (slug && !validSlug) ? 'text-destructive' : 'text-muted-foreground'}`}
            >
              {duplicate
                ? 'This slug already exists.'
                : 'Use lowercase letters and numbers separated by single underscores.'}
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="new-position">Position</Label>
            <Select value={after} onValueChange={setAfter}>
              <SelectTrigger id="new-position" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="beginning">At the beginning</SelectItem>
                {lessons.map((lesson) => (
                  <SelectItem key={lesson.lessonId} value={lesson.lessonId}>
                    After {lesson.title}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {disabledReason ? (
            <p
              id="new-lesson-disabled-reason"
              className={`text-sm ${invalidField?.id === 'new-slug' ? 'text-destructive' : 'text-muted-foreground'}`}
            >
              {disabledReason}
            </p>
          ) : null}
          <DialogFooter className="[&>button]:w-full sm:[&>button]:w-auto">
            <Button type="button" variant="dialogSecondary" onClick={onClose}>
              Cancel
            </Button>
            <Button
              type="submit"
              aria-disabled={!valid}
              className={
                !valid
                  ? 'border border-border bg-control-disabled text-muted-foreground shadow-none hover:bg-control-disabled'
                  : undefined
              }
              aria-describedby={disabledReason ? 'new-lesson-disabled-reason' : undefined}
            >
              Create lesson
            </Button>
          </DialogFooter>
        </form>
      )}
    </DialogContent>
  );
}
