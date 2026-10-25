import * as React from 'react';
import { toast } from 'sonner';
import type { LessonGateBlocker, LessonPayload } from '@/api/contracts';
import { ApiError } from '@/api/http';
import { navigate } from '@/app/router';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { StatusChip } from '@/components/workbench';
import { JobErrorDisclosure } from '@/jobs/job-error-disclosure';
import { useRegeneration } from '@/jobs/use-regeneration';
import { useLessonAction } from '@/lesson/api';
import { formatLessonGateBlocker } from '@/lesson/lifecycle/format-lesson-gate-blocker';

export type LifecycleDialog = 'promote' | 'regenerate' | 'regenerate-exercises' | 'discard' | null;
type DisplayDialog = LifecycleDialog | 'canonical-changed';

export function LifecycleDialogs({
  lesson,
  dialog,
  onDialog,
  regenerationScope,
  sectionKey,
  itemKey,
  exerciseKeys,
  busy,
}: {
  lesson: LessonPayload;
  dialog: LifecycleDialog;
  onDialog: (dialog: LifecycleDialog) => void;
  regenerationScope: 'lesson' | 'section' | 'item';
  sectionKey: string | null;
  itemKey: string | null;
  exerciseKeys: string[];
  busy: boolean;
}) {
  const action = useLessonAction(lesson.lessonId);
  const regeneration = useRegeneration(lesson.lessonId);
  const [conflict, setConflict] = React.useState<string | null>(null);
  const [scope, setScope] = React.useState<'lesson' | 'section' | 'item'>('section');
  const display: DisplayDialog = conflict !== null ? 'canonical-changed' : dialog;
  const key = scope === 'lesson' ? 'whole' : scope === 'section' ? sectionKey : itemKey;
  const pending = action.isPending || regeneration.start.isPending || busy;
  const close = () => {
    setConflict(null);
    onDialog(null);
  };
  const goToBlocker = (blocker: LessonGateBlocker) => {
    close();
    switch (blocker.target) {
      case 'proposal':
        requestAnimationFrame(() => {
          const proposal = lesson.state.proposals.find(
            (item) => item.status === 'pending' || item.status === 'conflicted',
          );
          const target =
            proposal &&
            [...document.querySelectorAll<HTMLElement>('[data-proposal-id]')].find(
              (item) => item.dataset.proposalId === proposal.proposalId,
            );
          target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
          target?.focus({ preventScroll: true });
        });
        return;
      case 'findings':
        requestAnimationFrame(() => {
          document.getElementById('findings-title')?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
        return;
      case 'audio':
        requestAnimationFrame(() =>
          document
            .querySelector('[aria-label="Audio status"]')
            ?.scrollIntoView({ behavior: 'smooth', block: 'center' }),
        );
        return;
      case 'canonical':
        mutate('promote');
        return;
      case 'draft':
      case 'editor':
        return;
      default: {
        const exhaustive: never = blocker;
        return exhaustive;
      }
    }
  };
  React.useEffect(() => {
    if (dialog === 'regenerate') setScope(regenerationScope);
  }, [dialog, regenerationScope]);
  const beginRegeneration = (request: { mode: 'scope'; key: string } | { mode: 'exercises'; exerciseKeys: string[] }) =>
    regeneration.start.mutate(request, {
      onSuccess: () => {
        close();
        toast('Regeneration started.');
      },
      onError: (error) => toast.error(error.message),
    });
  const mutate = (route: string, body: object = {}) =>
    action.mutate(
      { route, body },
      {
        onSuccess: (result) => {
          close();
          if (result.message) toast.success(result.message);
          if (result.commit)
            toast.success(`${route === 'promote' ? 'Promoted' : 'Discarded'} ${result.commit.slice(0, 7)}`);
          if (result.dist_files) toast.success(`Regenerated ${result.dist_files.length} dist files`);
          if (route === 'discard') navigate('/');
        },
        onError: (error) => {
          if (error instanceof ApiError && error.code === 'canonical_changed') {
            onDialog(null);
            setConflict(error.diff ?? 'Canonical source changed since this draft was started.');
          } else toast.error(error.message);
        },
      },
    );
  const run = regeneration.run.data;
  const activeRegenerationJobId = regeneration.jobId;
  return (
    <>
      {run ? (
        <div
          role="status"
          aria-label="Regeneration progress"
          className="flex flex-wrap items-center gap-2 border-b px-5 py-2 text-sm"
        >
          <span>Regenerate {run.mode === 'exercises' ? 'stale exercises' : run.key}</span>
          {run.steps.map((step, index) => (
            <StatusChip key={`${step}-${index}`} tone={step === 'waiting for you' ? 'warning' : 'ok'}>
              {step}
            </StatusChip>
          ))}
          {run.status === 'running' && run.next.length ? <span>{run.next.join(', ')}…</span> : null}
          {run.status === 'done' ? <span>Completed · {run.decision}</span> : null}
          {run.status === 'shutdown_interrupted' ? (
            <span>Interrupted by server shutdown · will resume on next start</span>
          ) : null}
          {run.status === 'running' && activeRegenerationJobId ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={regeneration.cancel.isPending}
              onClick={() =>
                void regeneration.cancel
                  .mutateAsync()
                  .catch((error: unknown) =>
                    toast.error(error instanceof Error ? error.message : 'Could not cancel regeneration.'),
                  )
              }
            >
              Cancel
            </Button>
          ) : null}
          {run.status === 'cancelled' ? <span>Regeneration cancelled</span> : null}
          {run.status !== 'cancelled' && run.status !== 'shutdown_interrupted' && run.errors.length ? (
            <span className="text-destructive-90">{run.errors.join(' · ')}</span>
          ) : null}
        </div>
      ) : null}
      {regeneration.run.isError ? (
        <div role="alert" className="px-5 py-2 text-sm text-destructive">
          <p>{regeneration.run.error.message}</p>
          <JobErrorDisclosure
            detail={regeneration.run.error instanceof ApiError ? regeneration.run.error.details : null}
          />
        </div>
      ) : null}
      <Dialog open={display !== null} onOpenChange={(open) => !open && close()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {display === 'promote'
                ? 'Promote draft'
                : display === 'regenerate'
                  ? 'Regenerate scope'
                  : display === 'regenerate-exercises'
                    ? 'Regenerate stale exercises'
                    : display === 'discard'
                      ? 'Discard draft?'
                      : 'Canonical lesson changed'}
            </DialogTitle>
            <DialogDescription>
              {display === 'promote'
                ? 'Promote saves this draft as canonical source after checks, proposal decisions, and audio are complete. Git records the change; release runs through the CLI.'
                : display === 'regenerate'
                  ? 'Generate a checked, AI-reviewed proposal. Review its changes before accepting.'
                  : display === 'regenerate-exercises'
                    ? 'Generate one proposal block for each stale exercise. The current exercises stay unchanged until you accept each block.'
                    : display === 'discard'
                      ? 'Delete this draft in one commit. Its committed history stays in git.'
                      : 'Review the differences before pulling changes into your draft or discarding it.'}
            </DialogDescription>
          </DialogHeader>
          {display === 'promote' && (lesson.state.promotionBlockers?.length ?? 0) > 0 ? (
            <section
              aria-label="Promotion blockers"
              className="rounded-xl border border-destructive-30 bg-destructive-0 p-4 text-sm"
            >
              <h2 className="m-0 font-semibold text-destructive-90">Promotion blocked</h2>
              <ul className="mb-0 mt-2 list-disc space-y-1 pl-5 text-destructive-90">
                {lesson.state.promotionBlockers?.map((blocker, index) => (
                  <li
                    key={`${blocker.kind}-${blocker.stage ?? 'none'}-${index}`}
                    className="flex flex-wrap items-center gap-x-2"
                  >
                    <span>{formatLessonGateBlocker(blocker)}</span>
                    {blockerActionLabel(blocker) ? (
                      <button
                        type="button"
                        className="font-semibold underline underline-offset-2"
                        onClick={() => goToBlocker(blocker)}
                      >
                        {blockerActionLabel(blocker)}
                      </button>
                    ) : null}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
          {display === 'canonical-changed' ? (
            <pre className="max-h-[45vh] overflow-auto rounded-lg border p-3 text-xs whitespace-pre-wrap break-words">
              {conflict}
            </pre>
          ) : null}
          {display === 'regenerate' ? (
            <>
              <Select
                value={scope}
                onValueChange={(value) => {
                  if (value === 'lesson' || value === 'section' || value === 'item') setScope(value);
                }}
              >
                <SelectTrigger aria-label="Regenerate scope">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="lesson">Whole lesson</SelectItem>
                  <SelectItem value="section">Current section</SelectItem>
                  <SelectItem value="item">Selected block</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-sm text-muted-foreground">
                {scope === 'lesson'
                  ? 'All authored lesson blocks will be regenerated.'
                  : (key ??
                    (scope === 'section' ? 'Scroll to a lesson section first.' : 'Focus a lesson block first.'))}
              </p>
              {regeneration.start.isError ? (
                <p role="alert" className="text-destructive-90">
                  {regeneration.start.error.message}
                </p>
              ) : null}
            </>
          ) : null}
          {display === 'regenerate-exercises' ? (
            <p className="text-sm text-muted-foreground">
              {exerciseKeys.length} stale exercise{exerciseKeys.length === 1 ? '' : 's'} will be checked together.
            </p>
          ) : null}
          <DialogFooter>
            <Button variant="dialogSecondary" onClick={close} disabled={pending}>
              Cancel
            </Button>
            {display === 'promote' ? (
              <Button disabled={pending || !lesson.state.promotable} onClick={() => mutate('promote')}>
                Promote
              </Button>
            ) : null}
            {display === 'discard' ? (
              <Button variant="destructive" disabled={pending} onClick={() => mutate('discard', { confirmed: true })}>
                Discard draft
              </Button>
            ) : null}
            {display === 'canonical-changed' ? (
              <>
                <Button
                  variant="destructive"
                  disabled={pending}
                  onClick={() => {
                    setConflict(null);
                    onDialog('discard');
                  }}
                >
                  Discard draft
                </Button>
                <Button disabled={pending} onClick={() => mutate('pull')}>
                  Pull changes into draft
                </Button>
              </>
            ) : null}
            {display === 'regenerate' ? (
              <Button
                disabled={pending || (scope !== 'lesson' && key === null) || run?.status === 'running'}
                onClick={() => {
                  if (key) beginRegeneration({ mode: 'scope', key });
                }}
              >
                Regenerate
              </Button>
            ) : null}
            {display === 'regenerate-exercises' ? (
              <Button
                disabled={pending || exerciseKeys.length === 0 || run?.status === 'running'}
                onClick={() => beginRegeneration({ mode: 'exercises', exerciseKeys })}
              >
                Regenerate stale exercises
              </Button>
            ) : null}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

function blockerActionLabel(blocker: LessonGateBlocker): string | null {
  switch (blocker.target) {
    case 'proposal':
      return 'Go to proposal';
    case 'findings':
      return 'Go to check findings';
    case 'audio':
      return 'Open audio status';
    case 'canonical':
      return 'Review canonical changes';
    case 'draft':
    case 'editor':
      return null;
    default: {
      const exhaustive: never = blocker;
      return exhaustive;
    }
  }
}
