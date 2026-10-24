/** Entry point: `LessonRow`, the memoized renderer for one lesson document row. */

import * as React from 'react';
import type {
  BlockExplanation,
  ExerciseAlignmentState,
  FindingData,
  LessonPayload,
  ProposalData,
  WorkbenchAudioClip,
} from '@/api/contracts';
import { Button } from '@/components/ui/button';
import { AudioClipControl } from '@/lesson/audio/lesson-audio';
import { BlockView } from '@/lesson/document/render/blocks';
import { ExercisePayload, Stimulus } from '@/lesson/document/render/exercise';
import { Spans } from '@/lesson/document/render/spans';
import { BlockEditCard } from '@/lesson/editing/block-edit-card';
import { ExerciseAlignmentNotice } from '@/lesson/lifecycle/exercise-alignment-notice';
import type { KeepDecision } from '@/lesson/review/decision-reasons';
import { MarginNote } from '@/lesson/review/margin-note';
import { isAcceptedProposalUnreviewed, ProposalCard, type ProposalVerdict } from '@/lesson/review/proposal-card';
import { cn } from '@/lib/utils';
import type { LessonRowIndexes } from './indexes';
import type { DocRow } from './rows';

export interface LessonRowProps {
  row: DocRow;
  lessonId: string;
  blockSource?: LessonPayload['blocks'][number];
  findings: FindingData[];
  proposals: ProposalData[];
  audioClips: WorkbenchAudioClip[];
  alignment?: ExerciseAlignmentState;
  explanation?: BlockExplanation;
  similarFindingsByCodeAndExcerpt: LessonRowIndexes['openAiFindingsByCodeAndExcerpt'];
  reviewMode: boolean;
  flag: 'error' | 'warning' | null;
  hasProposal: boolean;
  isOpen: boolean;
  isEdited: boolean;
  currentFindingId: string | null;
  actionsBusy?: boolean;
  agentBusy?: boolean;
  explanationBusy?: boolean;
  onOpenRow: (rowId: string) => void;
  onCloseRow: () => void;
  onDirtyChange: (dirty: boolean) => void;
  onEdited: (rowId: string) => void;
  onResolve: (finding: FindingData) => void;
  onResolveSimilar: (findings: FindingData[]) => void;
  onFix: (finding: FindingData) => void;
  onAskChat: (finding: FindingData) => void;
  onExplain: (blockId: string) => void;
  onSettle: (proposal: ProposalData, verdict: ProposalVerdict) => void;
  onKeepExercise: (exerciseKey: string, decision: KeepDecision) => void;
  onKeepProposal: (proposal: ProposalData, decision: KeepDecision) => void;
  onGenerateAudio: (rowKey: string) => void;
  onReviewChanged?: () => void;
  uncoveredKeys?: readonly string[];
  lessonBlockCount?: number;
}

export const LessonRow = React.memo(function LessonRow(props: LessonRowProps) {
  const {
    row,
    lessonId,
    blockSource,
    findings,
    proposals,
    audioClips,
    alignment,
    explanation,
    similarFindingsByCodeAndExcerpt,
    reviewMode,
    flag,
    hasProposal,
    isOpen,
    isEdited,
    currentFindingId,
    actionsBusy,
    agentBusy,
    explanationBusy,
  } = props;
  const onProposalSimilar = (finding: FindingData) => {
    const group = similarFindingsByCodeAndExcerpt.get(finding.code)?.get(finding.excerpt);
    if (!group || group.length < 2) return undefined;
    return () =>
      props.onResolveSimilar([finding, ...group.filter((similar) => similar.findingId !== finding.findingId)]);
  };

  return (
    <>
      <div
        className={cn(
          'relative -ml-[calc(0.75rem+3px)] min-w-0 border-l-[3px] border-transparent pl-3',
          reviewMode && flag === 'warning' && 'border-warning-50',
          reviewMode && flag === 'error' && 'border-destructive-60',
          hasProposal && 'border-primary-60',
        )}
      >
        {/* biome-ignore lint/a11y/useSemanticElements: Preserve the named row group used for author navigation. */}
        <div
          role="group"
          aria-label={
            row.kind === 'block' ? 'Block body' : row.kind === 'exercise' ? 'Exercise body' : 'Section heading'
          }
          className="relative flow-root min-w-0"
        >
          {/* biome-ignore lint/a11y/useAriaPropsSupportedByRole: Preserve the existing label for block action controls. */}
          <div
            aria-label="Block actions"
            className="absolute top-4 right-4 z-10 flex min-h-6 items-center justify-end gap-2"
          >
            {isEdited ? (
              <span className="text-[0.6rem] font-bold tracking-[0.12em] text-primary-80 uppercase">edited</span>
            ) : null}
            {!isOpen ? (
              <Button
                size="sm"
                variant="outline"
                type="button"
                aria-label={row.kind === 'exercise' ? 'Edit exercise' : 'Edit block'}
                onClick={() => props.onOpenRow(row.rowId)}
                className="h-6 px-2 text-xs opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 focus-visible:opacity-100"
              >
                Edit
              </Button>
            ) : null}
          </div>
          <RowBody
            row={row}
            alignment={alignment}
            actionsBusy={actionsBusy}
            onKeepExercise={props.onKeepExercise}
            afterReading={
              row.blockKind === 'reading'
                ? audioClips.map((clip) => (
                    <AudioClipControl key={clip.binding} clip={clip} onGenerate={props.onGenerateAudio} />
                  ))
                : undefined
            }
          />
          {row.blockKind !== 'reading'
            ? audioClips.map((clip) => (
                <AudioClipControl key={clip.binding} clip={clip} onGenerate={props.onGenerateAudio} />
              ))
            : null}
          {row.kind === 'exercise' || row.blockKind === 'reading' ? (
            <div className="mt-3">
              {explanation ? (
                <section
                  aria-label="AI explanation"
                  className="rounded-xl border border-primary-20 bg-primary-0 p-3 text-sm"
                >
                  <p className="m-0 text-xs font-semibold tracking-wide text-primary-90 uppercase">
                    AI explanation · not a qualified Bokmål review
                  </p>
                  <dl className="mt-2 grid gap-2">
                    {explanation.translations.map((item, index) => (
                      <div key={`${item.source}-${index}`}>
                        <dt lang="nb" className="font-medium">
                          {item.source}
                        </dt>
                        <dd lang="en" className="m-0 text-muted-foreground">
                          {item.english}
                        </dd>
                      </div>
                    ))}
                  </dl>
                  <p lang="en" className="mt-2 mb-0">
                    <strong>What it tests:</strong> {explanation.itemTested}
                  </p>
                </section>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={explanationBusy || actionsBusy}
                  onClick={() => props.onExplain(row.targetKey)}
                >
                  Explain in English
                </Button>
              )}
            </div>
          ) : null}
          {isOpen ? (
            <BlockEditCard
              row={row}
              lessonId={lessonId}
              blockSource={blockSource}
              onCancel={props.onCloseRow}
              onDirtyChange={props.onDirtyChange}
              onDone={(changed) => {
                if (changed) props.onEdited(row.rowId);
                props.onCloseRow();
              }}
            />
          ) : null}
          {proposals.map((proposal) => (
            <ProposalCard
              key={`${proposal.proposalId}-${proposal.key}`}
              className="my-3"
              proposal={proposal}
              disabled={actionsBusy}
              onAccept={() => props.onSettle(proposal, 'accepted')}
              onReject={() => props.onSettle(proposal, 'rejected')}
              onKeepAsIs={(_item, note) => props.onKeepProposal(proposal, note)}
              uncoveredKeys={props.uncoveredKeys}
              lessonBlockCount={props.lessonBlockCount}
              onReviewChanged={
                isAcceptedProposalUnreviewed(proposal, props.uncoveredKeys ?? []) ? props.onReviewChanged : undefined
              }
              exerciseLabel={row.kind === 'exercise' ? `Exercise · ${row.element.operation}` : row.sectionLabel}
              exercisePrompt={row.kind === 'exercise' ? <Spans values={row.element.prompt} /> : undefined}
            />
          ))}
        </div>
      </div>
      {reviewMode ? (
        <div className="flex min-w-0 flex-col gap-2 pt-3">
          {findings.map((finding) => {
            const group =
              finding.origin === 'ai' && finding.status === 'open'
                ? similarFindingsByCodeAndExcerpt.get(finding.code)?.get(finding.excerpt)
                : undefined;
            const similarCount = group ? group.length - 1 : 0;
            return (
              <MarginNote
                key={finding.findingId}
                id={`finding-${finding.findingId}`}
                finding={finding}
                isCurrent={currentFindingId === finding.findingId}
                similarFindingCount={similarCount}
                onAskChat={() => props.onAskChat(finding)}
                disabled={actionsBusy || agentBusy}
                onResolve={finding.origin === 'ai' ? () => props.onResolve(finding) : undefined}
                onResolveSimilar={onProposalSimilar(finding)}
                onFix={finding.origin === 'ai' ? () => props.onFix(finding) : undefined}
              />
            );
          })}
        </div>
      ) : null}
    </>
  );
});

function RowBody({
  row,
  alignment,
  actionsBusy,
  onKeepExercise,
  afterReading,
}: {
  row: DocRow;
  alignment?: ExerciseAlignmentState;
  actionsBusy?: boolean;
  onKeepExercise: (exerciseKey: string, decision: KeepDecision) => void;
  afterReading?: React.ReactNode;
}): React.ReactNode {
  if (row.kind === 'heading') {
    return (
      <div className="min-w-0 max-w-[calc(100%-3.5rem)]">
        <span className="inline-flex rounded-full bg-secondary-20 px-2.5 py-1 text-[0.68rem] font-semibold tracking-[0.12em] text-secondary-90 uppercase">
          {String(row.element.role ?? 'section').replaceAll('_', ' ')}
        </span>
        <h2 lang="en" className="mt-2 mb-1 text-2xl leading-tight">
          {String(row.element.title ?? '')}
        </h2>
      </div>
    );
  }
  if (row.kind === 'exercise') {
    const operation = String(row.element.operation ?? 'exercise');
    const operationLabel = operation === 'generate_exercise' ? 'Exercise' : operation.replaceAll('_', ' ');
    return (
      <article
        aria-label={operationLabel === 'Exercise' ? 'Exercise' : `Exercise · ${operationLabel}`}
        className="rounded-2xl border bg-card p-5 shadow-sm"
      >
        <div className="flex items-center justify-between gap-3 text-sm">
          <span className="rounded-full bg-primary-20 px-2.5 py-1 text-[0.68rem] font-semibold tracking-[0.12em] text-primary-90 uppercase">
            {operationLabel}
          </span>
          <span className="mr-14 min-w-0 break-words text-right text-muted-foreground">
            {String(row.element.objective_id ?? '').replaceAll('_', ' ')}
          </span>
        </div>
        <Stimulus items={row.element.stimulus} />
        <h2 lang="en" className="mt-3 mb-4 text-lg leading-normal font-semibold whitespace-pre-line">
          <Spans values={row.element.prompt} />
        </h2>
        <div className="rounded-2xl bg-secondary-0 p-5">
          <ExercisePayload operation={operation} data={(row.element.payload as Record<string, unknown>) ?? {}} />
        </div>
        <ExerciseAlignmentNotice
          alignment={alignment}
          exerciseKey={row.targetKey}
          exerciseLabel={`Exercise · ${operationLabel}`}
          exercisePrompt={<Spans values={row.element.prompt} />}
          disabled={actionsBusy}
          onKeep={onKeepExercise}
        />
        {row.element.explanation ? (
          <details className="mt-4 border-t pt-4 text-sm">
            <summary className="cursor-pointer text-[0.68rem] font-semibold tracking-[0.16em] text-secondary-80 uppercase">
              Author explanation
            </summary>
            <div className="mt-2.5 text-muted-foreground">
              <Spans values={row.element.explanation} />
            </div>
          </details>
        ) : null}
      </article>
    );
  }
  return row.block ? (
    <div
      className={cn('min-w-0', ['paragraph', 'heading', 'list'].includes(row.blockKind) && 'max-w-[calc(100%-3.5rem)]')}
    >
      <BlockView block={row.block} afterReading={afterReading} />
    </div>
  ) : null;
}
