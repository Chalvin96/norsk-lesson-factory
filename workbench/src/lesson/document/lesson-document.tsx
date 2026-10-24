/** Entry point: `LessonDocument`, the memoized author document renderer. */

import * as React from 'react';
import type { FindingData, LessonPayload, ProposalData, WorkbenchAudioClip } from '@/api/contracts';
import { VoiceLegend } from '@/lesson/audio/lesson-audio';
import { SourceEditor } from '@/lesson/editing/source-editor';
import type { LessonUi } from '@/lesson/lesson-ui';
import type { KeepDecision } from '@/lesson/review/decision-reasons';
import { isAcceptedProposalUnreviewed, ProposalCard, type ProposalVerdict } from '@/lesson/review/proposal-card';
import { cn } from '@/lib/utils';
import type { LessonRowIndexes } from './indexes';
import { LessonRow } from './lesson-row';
import { PlanView } from './plan-view';
import type { DocRow } from './rows';

const K_LESSON_DOCUMENT_KICKER = 'text-xs font-semibold tracking-[0.16em] text-muted-foreground uppercase';
const K_LESSON_DOCUMENT_LEARNER_VIEW = React.lazy(() =>
  import('@/lesson/document/render/learner/learner-view').then(({ LearnerView }) => ({ default: LearnerView })),
);
const K_LESSON_DOCUMENT_EMPTY_FINDINGS: FindingData[] = [];
const K_LESSON_DOCUMENT_EMPTY_PROPOSALS: ProposalData[] = [];
const K_LESSON_DOCUMENT_EMPTY_AUDIO_CLIPS: WorkbenchAudioClip[] = [];

export interface LessonDocumentProps {
  lesson: LessonPayload;
  rows: DocRow[];
  indexes: LessonRowIndexes;
  ui: LessonUi;
  rowRef: (rowId: string) => (element: HTMLDivElement | null) => void;
  onOpenRow: (rowId: string) => void;
  onCloseRow: () => void;
  onDirtyChange: (dirty: boolean) => void;
  onEdited: (rowId: string) => void;
  onSourceBusyChange: (busy: boolean) => void;
  onResolve: (finding: FindingData) => void;
  onResolveSimilar: (findings: FindingData[]) => void;
  actionsBusy?: boolean;
  agentBusy?: boolean;
  onFix: (finding: FindingData) => void;
  onAskChat: (finding: FindingData) => void;
  onExplain: (blockId: string) => void;
  explanationBusy?: boolean;
  onSettle: (proposal: ProposalData, verdict: ProposalVerdict) => void;
  onKeepExercise: (exerciseKey: string, decision: KeepDecision) => void;
  onKeepProposal: (proposal: ProposalData, decision: KeepDecision) => void;
  onGenerateAudio: (rowKey: string) => void;
  onReviewChanged?: () => void;
}

export const LessonDocument = React.memo(function LessonDocument(props: LessonDocumentProps) {
  const { lesson, rows, indexes, ui } = props;
  const [sourceOpened, setSourceOpened] = React.useState(false);
  React.useEffect(() => {
    if (ui.view === 'source') setSourceOpened(true);
  }, [ui.view]);
  const learnerView = ui.stage === 'lesson' && ui.view === 'learner';
  const review = ui.mode === 'review';
  const sideMargin = review ? 'lg:mr-[calc(var(--margin-width)+1.5rem)]' : '';
  const findingCount = lesson.sourceFindings.length + lesson.errors.length;
  const aiFindings = React.useMemo(
    () => lesson.state.findings.filter((finding) => finding.origin === 'ai'),
    [lesson.state.findings],
  );
  const uncoveredKeys = lesson.state.reviewCoverage?.uncovered ?? [];
  return (
    <div
      className={
        learnerView
          ? undefined
          : cn(
              'mx-auto px-3 pt-5 pb-32 sm:px-8 sm:pt-7 [--margin-width:clamp(13rem,16vw,17rem)]',
              review ? 'max-w-[calc(44rem+1.5rem+var(--margin-width))]' : 'max-w-[44rem]',
            )
      }
    >
      {learnerView ? (
        <React.Suspense
          fallback={
            <p role="status" className="p-6 text-center text-sm text-muted-foreground">
              Loading learner view…
            </p>
          }
        >
          <K_LESSON_DOCUMENT_LEARNER_VIEW lesson={lesson} />
        </React.Suspense>
      ) : (
        <>
          <header className={sideMargin}>
            <span className={K_LESSON_DOCUMENT_KICKER}>
              {lesson.source === 'draft' ? 'Draft' : 'Canonical'} ·{' '}
              <code className="rounded bg-secondary-10 px-1.5 text-[0.85em] tracking-normal break-all normal-case">
                {lesson.sourceLabel}
              </code>
            </span>
            <h1 id="lesson-title" className="mt-2 mb-3 text-3xl leading-tight">
              {lesson.lesson?.title ?? lesson.lessonId}
            </h1>
            <div className="flex gap-1.5 text-xs">
              <span className="rounded-full border bg-secondary-10 px-2 py-0.5 font-semibold">
                {lesson.lesson?.cefrLevel ?? '—'}
              </span>
              <span className="rounded-full border bg-secondary-10 px-2 py-0.5 font-semibold capitalize">
                {(lesson.lesson?.kind ?? 'source').replaceAll('_', ' ')}
              </span>
            </div>
            {lesson.lesson?.goal ? (
              <p lang="en" className="mt-4 text-secondary-80">
                {lesson.lesson.goal}
              </p>
            ) : null}
            <VoiceLegend status={lesson.audio} />
          </header>
          {lesson.state.exerciseStaleness?.some((item) => item.status === 'unknown') ? (
            <aside
              role="status"
              className={cn('mt-5 rounded-xl border border-warning-40 bg-warning-10 p-4 text-sm', sideMargin)}
            >
              <h2 className="m-0 text-xs font-bold tracking-[0.14em] text-warning-90 uppercase">
                Staleness unavailable
              </h2>
              <p className="mt-2 mb-0 text-warning-90">
                {lesson.state.exerciseStaleness.find((item) => item.status === 'unknown')?.reason}
              </p>
            </aside>
          ) : null}
          {lesson.state.proposals
            .filter((proposal) => ['plan.md', 'lesson.md', 'exercises.yaml'].includes(proposal.key))
            .map((proposal) => (
              <section
                key={`${proposal.proposalId}-${proposal.key}`}
                aria-label={`${proposal.key} proposal`}
                className="my-5"
              >
                <h2 className="text-lg">{proposal.key}</h2>
                <ProposalCard
                  proposal={proposal}
                  disabled={props.actionsBusy}
                  onAccept={() => props.onSettle(proposal, 'accepted')}
                  onReject={() => props.onSettle(proposal, 'rejected')}
                  uncoveredKeys={uncoveredKeys}
                  lessonBlockCount={lesson.blocks.length}
                  onReviewChanged={
                    isAcceptedProposalUnreviewed(proposal, uncoveredKeys) ? props.onReviewChanged : undefined
                  }
                />
              </section>
            ))}
          {review && lesson.state.reviewSummary ? (
            <section aria-label="AI review summary" className="my-5 rounded-xl border bg-white p-4 text-sm">
              <p>{lesson.state.reviewSummary}</p>
              <p className="mt-1 text-xs text-muted-foreground">
                {lesson.state.reviewCreatedAt} · source {lesson.state.reviewDigest?.slice(0, 12)}
              </p>
              <p className="mt-2">
                {aiFindings.filter((finding) => finding.status === 'open').length} open ·{' '}
                {aiFindings.filter((finding) => finding.status === 'resolved').length} resolved ·{' '}
                {aiFindings.filter((finding) => finding.level === 'blocking').length} blocking ·{' '}
                {aiFindings.filter((finding) => finding.level === 'major').length} major ·{' '}
                {aiFindings.filter((finding) => finding.level === 'minor').length} minor
              </p>
              <p className="mt-2 text-xs text-muted-foreground">AI judgment, not a qualified Bokmål review.</p>
            </section>
          ) : null}
          {findingCount > 0 && ui.stage === 'lesson' && ui.view !== 'source' ? (
            <section
              aria-labelledby="findings-title"
              className={cn('mt-6 rounded-2xl border border-warning-30 bg-warning-10 p-4', sideMargin)}
            >
              <h2 id="findings-title" className={`${K_LESSON_DOCUMENT_KICKER} m-0 text-warning-90`}>
                {findingCount} source finding{findingCount === 1 ? '' : 's'}
              </h2>
              <div className="mt-3 grid gap-2">
                {lesson.sourceFindings.map((finding, index) => (
                  <div key={index} className="rounded-lg border border-warning-30 border-l-4 bg-white p-3 text-sm">
                    <div className="flex flex-wrap items-baseline gap-x-2">
                      <strong>{finding.code ?? 'finding'}</strong>
                      <span className="text-muted-foreground">
                        {finding.artifact ?? 'source'} · {finding.location ?? ''}
                      </span>
                    </div>
                    <p className="mt-1.5">{finding.explanation ?? finding.evidence ?? ''}</p>
                  </div>
                ))}
                {lesson.errors.map((error, index) => (
                  <div
                    key={`error-${index}`}
                    className="rounded-lg border border-destructive-40 border-l-4 bg-destructive-0 p-3 text-sm text-destructive-100"
                  >
                    <strong>compile-preview-error</strong>
                    <p className="mt-1.5">{error}</p>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
          {ui.stage === 'plan' ? (
            <section aria-labelledby="plan-title" className={cn('mt-6', sideMargin)}>
              <h2 id="plan-title" className="m-0 text-2xl">
                Plan
              </h2>
              <PlanView source={lesson.planSource} preview={lesson.planPreview} />
            </section>
          ) : ui.view === 'source' ? null : rows.length === 0 ? (
            <div role="status" className={cn('py-8 text-center text-warning-90', sideMargin)}>
              <strong>This lesson could not be compiled for display.</strong>
              <p className="mt-2 text-muted-foreground">
                Fix the source findings above and reload. No earlier version is shown.
              </p>
            </div>
          ) : (
            <div className="mt-6">
              {rows.map((row) => {
                const findings = indexes.findingsByTarget.get(row.targetKey) ?? K_LESSON_DOCUMENT_EMPTY_FINDINGS;
                const proposals = indexes.proposalsByTarget.get(row.targetKey) ?? K_LESSON_DOCUMENT_EMPTY_PROPOSALS;
                const currentFindingId = findings.some((finding) => finding.findingId === ui.currentFindingId)
                  ? ui.currentFindingId
                  : null;
                return (
                  <div
                    key={row.rowId}
                    id={row.rowId}
                    ref={props.rowRef(row.rowId)}
                    tabIndex={0}
                    data-row-kind={row.kind}
                    data-flag={(review && indexes.flagByTarget.get(row.targetKey)) || undefined}
                    data-proposal={indexes.pendingProposalByTarget.has(row.targetKey) || undefined}
                    className={cn(
                      'group grid scroll-mt-16 items-start gap-6',
                      review ? 'grid-cols-1 lg:grid-cols-[minmax(0,1fr)_var(--margin-width)]' : 'grid-cols-1',
                      row.kind === 'heading' && 'mt-10',
                      row.kind === 'exercise' && 'my-6',
                    )}
                  >
                    <LessonRow
                      row={row}
                      lessonId={lesson.lessonId}
                      blockSource={indexes.blocksByTarget.get(row.targetKey)}
                      findings={findings}
                      proposals={proposals}
                      audioClips={indexes.audioByTarget.get(row.targetKey) ?? K_LESSON_DOCUMENT_EMPTY_AUDIO_CLIPS}
                      alignment={indexes.stalenessByExercise.get(row.targetKey)}
                      explanation={lesson.state.explanations?.[row.targetKey]}
                      similarFindingsByCodeAndExcerpt={indexes.openAiFindingsByCodeAndExcerpt}
                      reviewMode={review}
                      flag={indexes.flagByTarget.get(row.targetKey) ?? null}
                      hasProposal={indexes.pendingProposalByTarget.has(row.targetKey)}
                      isOpen={ui.openRowId === row.rowId}
                      isEdited={ui.editedRowIds.includes(row.rowId)}
                      currentFindingId={currentFindingId}
                      actionsBusy={props.actionsBusy}
                      agentBusy={props.agentBusy}
                      explanationBusy={props.explanationBusy}
                      onOpenRow={props.onOpenRow}
                      onCloseRow={props.onCloseRow}
                      onDirtyChange={props.onDirtyChange}
                      onEdited={props.onEdited}
                      onResolve={props.onResolve}
                      onResolveSimilar={props.onResolveSimilar}
                      onFix={props.onFix}
                      onAskChat={props.onAskChat}
                      onExplain={props.onExplain}
                      onSettle={props.onSettle}
                      onKeepExercise={props.onKeepExercise}
                      onKeepProposal={props.onKeepProposal}
                      onGenerateAudio={props.onGenerateAudio}
                      onReviewChanged={props.onReviewChanged}
                      uncoveredKeys={uncoveredKeys}
                      lessonBlockCount={lesson.blocks.length}
                    />
                  </div>
                );
              })}
            </div>
          )}
        </>
      )}
      <section
        aria-label="Lesson text source"
        className={cn('mt-6', sideMargin, (ui.view !== 'source' || ui.stage !== 'lesson') && 'hidden')}
      >
        {sourceOpened || ui.view === 'source' ? (
          <SourceEditor lesson={lesson} onBusyChange={props.onSourceBusyChange} />
        ) : null}
      </section>
    </div>
  );
}, lessonDocumentPropsEqual);

function lessonDocumentPropsEqual(previous: LessonDocumentProps, next: LessonDocumentProps): boolean {
  return (Object.keys(previous) as (keyof LessonDocumentProps)[]).every((key) =>
    key === 'lesson' ? sameLessonDocumentData(previous.lesson, next.lesson) : Object.is(previous[key], next[key]),
  );
}

function sameLessonDocumentData(previous: LessonPayload, next: LessonPayload): boolean {
  return (
    previous.lessonId === next.lessonId &&
    previous.source === next.source &&
    previous.sourceLabel === next.sourceLabel &&
    previous.lesson === next.lesson &&
    previous.lessonSource === next.lessonSource &&
    previous.planSource === next.planSource &&
    previous.planPreview === next.planPreview &&
    previous.draft === next.draft &&
    previous.audio === next.audio &&
    previous.elements === next.elements &&
    previous.blocks === next.blocks &&
    previous.sourceFindings === next.sourceFindings &&
    previous.errors === next.errors &&
    previous.state.findings === next.state.findings &&
    previous.state.proposals === next.state.proposals &&
    previous.state.exerciseStaleness === next.state.exerciseStaleness &&
    previous.state.reviewSummary === next.state.reviewSummary &&
    previous.state.reviewCreatedAt === next.state.reviewCreatedAt &&
    previous.state.reviewDigest === next.state.reviewDigest &&
    previous.state.explanations === next.state.explanations
  );
}
