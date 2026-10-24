import type { WorkbenchProposal } from '@/api/contracts';
import { Button } from '@/components/ui/button';
import { StatusChip } from '@/components/workbench/status-chip';
import { KeepExerciseDialog } from '@/lesson/lifecycle/keep-exercise-dialog';
import type { KeepDecision } from '@/lesson/review/decision-reasons';
import { cn } from '@/lib/utils';
import { ProposalDiff } from './proposal-diff';

export type ProposalVerdict = 'accepted' | 'rejected';

const K_PROPOSAL_CARD_REVIEWED_FILE_KEYS = ['lesson.md', 'exercises.yaml'];

/** One accepted proposal whose changed blocks are not covered by the current AI review. */
export function isAcceptedProposalUnreviewed(proposal: WorkbenchProposal, uncoveredKeys: readonly string[]): boolean {
  if (proposal.status !== 'accepted') return false;
  if (uncoveredKeys.includes(proposal.key)) return true;
  return K_PROPOSAL_CARD_REVIEWED_FILE_KEYS.includes(proposal.key) && uncoveredKeys.length > 0;
}

export interface ProposalCardProps {
  proposal: WorkbenchProposal;
  verdict?: ProposalVerdict;
  onAccept?: (proposal: WorkbenchProposal) => void;
  onReject?: (proposal: WorkbenchProposal) => void;
  onKeepAsIs?: (proposal: WorkbenchProposal, decision: KeepDecision) => void;
  onReviewChanged?: (proposal: WorkbenchProposal) => void;
  uncoveredKeys?: readonly string[];
  lessonBlockCount?: number;
  exerciseLabel?: string;
  exercisePrompt?: React.ReactNode;
  disabled?: boolean;
  className?: string;
  kindLabel?: string;
}

export function ProposalCard({
  proposal,
  verdict,
  onAccept,
  onReject,
  onKeepAsIs,
  onReviewChanged,
  uncoveredKeys = [],
  lessonBlockCount = 0,
  exerciseLabel,
  exercisePrompt,
  className,
  disabled,
  kindLabel = 'AI proposal',
}: ProposalCardProps) {
  const wholeLesson = uncoveredKeys.some((key) => key.startsWith('@'));
  const uncoveredBlockCount = uncoveredKeys.filter((key) => !key.startsWith('@')).length;
  const settled =
    verdict ?? (proposal.status === 'accepted' || proposal.status === 'rejected' ? proposal.status : undefined);
  const conflicted = proposal.status === 'conflicted';
  const isRegeneration = proposal.origin === 'regenerate';
  const allowsAccept = proposal.allowedDecisions.includes('accept');
  const allowsReject = proposal.allowedDecisions.includes('reject');
  const allowsKeep = proposal.allowedDecisions.includes('keep');
  return (
    <section
      aria-label="AI proposal"
      data-proposal-id={proposal.proposalId}
      tabIndex={-1}
      data-verdict={settled ?? (conflicted ? 'conflicted' : undefined)}
      className={cn(
        'rounded-xl border border-primary-30 bg-primary-0 p-3',
        settled && 'border-dashed bg-transparent',
        className,
      )}
    >
      <div className="flex items-center gap-2 text-xs text-primary-90">
        <StatusChip tone="draft">{kindLabel}</StatusChip>
        <span>{proposal.note}</span>
      </div>
      {proposal.changeSummary ? (
        <dl
          aria-label={isRegeneration ? 'Regeneration decision support' : 'Decision support'}
          className="mt-2 grid gap-2 rounded-lg border border-primary-20 bg-white p-3 text-sm"
        >
          <div className="grid gap-1 sm:grid-cols-[5.5rem_minmax(0,1fr)]">
            <dt className="text-[0.65rem] font-bold tracking-[0.12em] text-secondary-80 uppercase">What changed</dt>
            <dd lang="en" className="m-0">
              {proposal.changeSummary}
            </dd>
          </div>
          <div className="grid gap-1 sm:grid-cols-[5.5rem_minmax(0,1fr)]">
            <dt className="text-[0.65rem] font-bold tracking-[0.12em] text-secondary-80 uppercase">Why</dt>
            <dd lang="en" className="m-0">
              {proposal.changedSections?.length
                ? proposal.changedSections.map((section) => (
                    <p key={section.sectionId} className="m-0">
                      {section.summary}
                    </p>
                  ))
                : allowsKeep
                  ? 'The exercise was regenerated against its changed section.'
                  : proposal.changeSummary}
            </dd>
          </div>
          <div className="grid gap-1 sm:grid-cols-[5.5rem_minmax(0,1fr)]">
            <dt className="text-[0.65rem] font-bold tracking-[0.12em] text-secondary-80 uppercase">Translation</dt>
            <dd className="m-0">
              {proposal.translations?.length
                ? proposal.translations.map((translation, index) => (
                    <p key={`${index}-${translation.norwegian}`} className="m-0">
                      <span lang="nb">{translation.norwegian}</span> — <span lang="en">{translation.english}</span>
                    </p>
                  ))
                : 'None'}
            </dd>
          </div>
          <div className="grid gap-1 sm:grid-cols-[5.5rem_minmax(0,1fr)]">
            <dt className="text-[0.65rem] font-bold tracking-[0.12em] text-secondary-80 uppercase">Answers</dt>
            <dd lang="en" className="m-0">
              {proposal.answerImpact ?? 'none'}
            </dd>
          </div>
          <div className="grid gap-1 sm:grid-cols-[5.5rem_minmax(0,1fr)]">
            <dt className="text-[0.65rem] font-bold tracking-[0.12em] text-secondary-80 uppercase">Unsure</dt>
            <dd lang="en" className="m-0">
              {proposal.uncertainty || 'None stated by the generator.'}
            </dd>
          </div>
        </dl>
      ) : null}
      <ProposalDiff
        before={proposal.beforeRendered ?? [{ text: proposal.before, lang: 'en' }]}
        after={proposal.afterRendered ?? [{ text: proposal.after, lang: 'en' }]}
      />
      <details className="mt-2 text-sm">
        <summary className="cursor-pointer text-muted-foreground">Raw source</summary>
        <div className="mt-2 grid grid-cols-2 gap-3">
          <section aria-label="Before source" className="min-w-0">
            <pre className="overflow-auto whitespace-pre-wrap break-words">{proposal.before}</pre>
          </section>
          <section aria-label="After source" className="min-w-0">
            <pre className="overflow-auto whitespace-pre-wrap break-words">{proposal.after}</pre>
          </section>
        </div>
      </details>
      {conflicted ? (
        <p role="status" className="mt-2 text-sm text-destructive-90">
          Conflicted: this block changed. Reject this suggestion.
        </p>
      ) : null}
      {proposal.check?.status === 'failed' ? (
        <p className="mt-2 text-sm text-destructive-90">Check failed: {proposal.check.errors.join(' · ')}</p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {settled ? (
          <>
            <StatusChip tone={settled === 'accepted' ? 'ok' : 'neutral'}>
              {settled === 'accepted' ? 'Accepted' : 'Rejected'}
            </StatusChip>
            {settled === 'accepted' && onReviewChanged ? (
              <Button
                size="sm"
                variant="outline"
                disabled={disabled}
                title={
                  wholeLesson
                    ? 'Run AI review on every block in this lesson because lesson structure is uncovered'
                    : 'Run AI review on all uncovered blocks in this lesson'
                }
                onClick={() => onReviewChanged(proposal)}
              >
                {wholeLesson
                  ? `Review whole lesson (${lessonBlockCount} blocks)`
                  : `Review ${uncoveredBlockCount} uncovered ${uncoveredBlockCount === 1 ? 'block' : 'blocks'}`}
              </Button>
            ) : null}
          </>
        ) : (
          <>
            {onAccept && allowsAccept ? (
              <Button disabled={disabled || conflicted} size="sm" onClick={() => onAccept(proposal)}>
                Accept
              </Button>
            ) : null}
            {onReject && allowsReject ? (
              <Button disabled={disabled} size="sm" variant="outline" onClick={() => onReject(proposal)}>
                Reject
              </Button>
            ) : null}
            {onKeepAsIs && allowsKeep ? (
              <KeepExerciseDialog
                exerciseLabel={exerciseLabel ?? 'Regenerated exercise'}
                exercisePrompt={exercisePrompt}
                changedSections={proposal.changedSections.map((section) => {
                  const heading = section.afterText?.split('\n')[0];
                  return heading?.startsWith('## ')
                    ? heading.replace(/^##\s+/, '').replace(/\s*\{.*\}\s*$/, '')
                    : section.sectionId;
                })}
                disabled={disabled}
                onKeep={(note) => onKeepAsIs(proposal, note)}
              />
            ) : null}
          </>
        )}
      </div>
    </section>
  );
}
