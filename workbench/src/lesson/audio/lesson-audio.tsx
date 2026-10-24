import { PauseIcon, PlayIcon } from 'lucide-react';
import * as React from 'react';
import type { WorkbenchAudioClip, WorkbenchAudioPlan, WorkbenchAudioStatus } from '@/api/contracts';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { StatusChip } from '@/components/workbench';
import { JobErrorDisclosure } from '@/jobs/job-error-disclosure';
import { useWorkbenchJob } from '@/jobs/use-workbench-job';
import { useLessonAction } from '@/lesson/api';

export function useLessonAudioWorkflow(lessonId: string) {
  const action = useLessonAction(lessonId);
  const synthesis = useWorkbenchJob(['lesson', lessonId]);
  const [plan, setPlan] = React.useState<WorkbenchAudioPlan | null>(null);

  const requestPlan = React.useCallback(
    async (selection: 'missing' | 'stale' | 'missing_and_stale', blockKey?: string) => {
      synthesis.reset();
      const result = await action.mutateAsync({
        route: 'audio/plan',
        body: {
          scope: blockKey ? 'block' : 'missing_and_stale',
          selection,
          ...(blockKey ? { block_key: blockKey } : {}),
        },
      });
      if (!result.plan) throw new Error('The server did not return an audio plan.');
      setPlan(result.plan);
    },
    [action.mutateAsync, synthesis.reset],
  );

  const startSynthesis = React.useCallback(() => {
    if (!plan) return;
    synthesis.mutate({
      route: `/api/lessons/${encodeURIComponent(lessonId)}/audio/synthesize`,
      body: { plan_token: plan.token },
    });
  }, [lessonId, plan, synthesis.mutate]);

  return {
    plan,
    setPlan,
    requestPlan,
    startSynthesis,
    planBusy: action.isPending,
    synthesis,
  };
}

export function LessonAudioPlanDialog({
  plan,
  onClose,
  onConfirm,
  busy,
  progress,
  completed,
  error,
  errorDetail,
}: {
  plan: WorkbenchAudioPlan | null;
  onClose: () => void;
  onConfirm: () => void;
  busy: boolean;
  progress: string[];
  completed: boolean;
  error: string | null;
  errorDetail?: string | null;
}) {
  return (
    <Dialog open={plan !== null} onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="w-[calc(100vw-2rem)] sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>{completed ? 'Audio checks complete' : 'Generate missing audio'}</DialogTitle>
          <DialogDescription>
            Audio is synthesized for the clips below and written to the draft. Nothing is published.
          </DialogDescription>
        </DialogHeader>
        {plan ? (
          <>
            <ol
              aria-label="Audio clips in this estimate"
              className="m-0 max-h-[38vh] list-none space-y-0 overflow-y-auto rounded-xl border border-primary-20 p-3"
            >
              {plan.clips.map((clip) => (
                <li
                  key={clip.binding}
                  className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1 border-b border-primary-10 py-2.5 first:pt-0 last:border-b-0 last:pb-0"
                >
                  <div className="min-w-0 flex-1">
                    <p lang="nb" className="m-0 break-words text-sm font-medium">
                      {clip.text}
                    </p>
                    {clip.translation ? (
                      <p
                        lang="en"
                        className="m-0 mt-0.5 break-words text-xs text-secondary-80 dark:text-muted-foreground"
                      >
                        {clip.translation}
                      </p>
                    ) : null}
                  </div>
                  <div className="flex basis-full items-center justify-between gap-3 text-xs text-secondary-80 dark:text-muted-foreground sm:basis-auto sm:justify-start">
                    <span>{clip.voice}</span>
                    <span>{clip.characters} chars</span>
                  </div>
                </li>
              ))}
            </ol>
            <dl className="m-0 grid grid-cols-1 gap-y-1.5 rounded-xl bg-secondary-10 dark:bg-control-disabled p-3 text-sm">
              <AudioSummaryRow label="New clips" value={String(plan.newCount)} />
              <AudioSummaryRow label="Reused (no cost)" value={String(plan.reusedCount)} />
              <AudioSummaryRow label="Characters to synthesize" value={String(plan.charactersToSynthesize)} />
              <AudioSummaryRow label="Estimated cost" value={currency(plan.estimatedCostUsd)} emphasize />
              <AudioSummaryRow
                label="Monthly spend / cap"
                value={`${currency(plan.monthlySpentUsd)} / ${currency(plan.monthlyCapUsd)}`}
              />
            </dl>
            <p className="m-0 text-xs text-secondary-80 dark:text-muted-foreground">
              Release still runs from the CLI and checks audio coverage there.
            </p>
            {plan.blockedReason ? (
              <p
                role="alert"
                className="m-0 rounded-lg border border-destructive-40 bg-destructive-0 p-3 text-sm text-destructive-90"
              >
                {plan.blockedReason}
              </p>
            ) : null}
          </>
        ) : null}
        {busy ? (
          <div role="status" aria-label="Audio synthesis progress" className="space-y-2 rounded-lg border p-3 text-sm">
            <p className="m-0 font-semibold">Generating audio…</p>
            <div className="flex flex-wrap gap-1.5">
              {progress.map((step, index) => (
                <StatusChip key={`${step}-${index}`} tone="ok">
                  {step}
                </StatusChip>
              ))}
            </div>
          </div>
        ) : null}
        {completed ? (
          <p role="status" className="m-0 text-sm text-accent-90">
            Audio was added to the draft. Review the clip states and any audio findings in the lesson.
          </p>
        ) : null}
        {error ? (
          <div
            role="alert"
            className="m-0 rounded-lg border border-destructive-40 bg-destructive-0 p-3 text-sm text-destructive-90"
          >
            <p className="m-0">{error}</p>
            <JobErrorDisclosure detail={errorDetail ?? null} />
          </div>
        ) : null}
        <DialogFooter>
          {!busy ? (
            <Button variant="dialogSecondary" onClick={onClose}>
              {completed || error ? 'Close' : 'Cancel'}
            </Button>
          ) : null}
          {!completed && !error ? (
            <Button disabled={!plan || busy || Boolean(plan.blockedReason)} onClick={onConfirm}>
              {busy ? 'Generating…' : `Generate ${plan?.newCount ?? 0} clip${plan?.newCount === 1 ? '' : 's'}`}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function AudioSummaryRow({ label, value, emphasize = false }: { label: string; value: string; emphasize?: boolean }) {
  return (
    <div className={`flex items-center justify-between gap-4 ${emphasize ? 'font-semibold' : ''}`}>
      <dt>{label}</dt>
      <dd className="m-0 text-right tabular-nums">{value}</dd>
    </div>
  );
}

function currency(value: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(value);
}

export function AudioClipControl({
  clip,
  onGenerate,
}: {
  clip: WorkbenchAudioClip;
  onGenerate: (rowKey: string) => void;
}) {
  const audioRef = React.useRef<HTMLAudioElement>(null);
  const [playing, setPlaying] = React.useState(false);
  const togglePlayback = () => {
    if (!audioRef.current) return;
    if (playing) {
      audioRef.current.pause();
      setPlaying(false);
      return;
    }
    void audioRef.current
      .play()
      .then(() => setPlaying(true))
      .catch(() => setPlaying(false));
  };
  const duration =
    clip.durationMs === null
      ? null
      : `${Math.floor(clip.durationMs / 60000)}:${String(Math.floor((clip.durationMs % 60000) / 1000)).padStart(2, '0')}`;
  return (
    <div className="mt-2.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-secondary-80">
      {clip.status === 'ready' ? (
        <>
          {/* biome-ignore lint/a11y/useMediaCaption: The authored utterance and translation remain visible beside playback. */}
          <audio
            className="sr-only"
            ref={audioRef}
            src={clip.playUrl}
            preload="none"
            onEnded={() => setPlaying(false)}
            onPause={() => setPlaying(false)}
          />
          <Button
            size="icon"
            variant="ghost"
            className="size-7"
            aria-label={`${playing ? 'Pause' : 'Play'} audio: ${clip.text}`}
            onClick={togglePlayback}
          >
            {playing ? <PauseIcon /> : <PlayIcon />}
          </Button>
          <span>
            Audio ready{duration ? ` · ${duration}` : ''} · voice {clip.voice}
          </span>
        </>
      ) : (
        <>
          <span
            className={
              clip.status === 'stale'
                ? 'rounded-full border border-warning-50 bg-warning-10 px-2 py-0.5 text-warning-90'
                : ''
            }
          >
            {clip.status === 'stale' ? 'Audio stale · text changed' : 'No audio yet'}
          </span>
          <Button size="sm" variant="outline" className="h-8" onClick={() => onGenerate(clip.rowKey)}>
            {clip.status === 'stale' ? 'Regenerate audio…' : 'Generate audio…'}
          </Button>
        </>
      )}
      {clip.findings.map((finding) => (
        <span key={finding.code} role="status" className="basis-full text-warning-90">
          Audio check · {finding.code.replaceAll('_', ' ')}: {finding.message}
        </span>
      ))}
    </div>
  );
}

export function VoiceLegend({ status }: { status: WorkbenchAudioStatus }) {
  if (!status.voices.length) return null;
  return (
    <section aria-label="Audio voice assignments" className="mt-3 space-y-1.5 text-xs text-secondary-80">
      <p className="m-0">
        <span className="font-semibold">Voices</span> ·{' '}
        {status.voices.map((voice) => `${voice.speakerName} · ${voice.voice}`).join(' · ')}
      </p>
      {status.voices
        .filter((voice) => voice.changedSinceRelease)
        .map((voice) => (
          <p
            key={voice.speakerId}
            role="status"
            className="m-0 rounded-lg border border-warning-40 bg-warning-10 px-3 py-2 text-warning-90"
          >
            Voice changed since last release · {voice.speakerName}: {voice.releasedVoice} → {voice.voice}
          </p>
        ))}
    </section>
  );
}
