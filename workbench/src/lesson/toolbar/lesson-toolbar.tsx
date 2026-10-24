/** Entry point: `LessonToolbar`. */

import { ChevronDownIcon, ChevronUpIcon, EllipsisIcon } from 'lucide-react';
import * as React from 'react';

import type { LessonGateBlocker, LessonState, ReviewScope } from '@/api/contracts';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Kbd, KbdGroup } from '@/components/ui/kbd';
import { Separator } from '@/components/ui/separator';
import { SegmentedControl, StatusChip } from '@/components/workbench';
import { JobErrorDisclosure } from '@/jobs/job-error-disclosure';
import { formatLessonGateBlocker } from '@/lesson/lifecycle/format-lesson-gate-blocker';
import { isAcceptedProposalUnreviewed } from '@/lesson/review/proposal-card';

import type { LessonUi, Mode, Stage, View } from '../lesson-ui';

const K_LESSON_TOOLBAR_PROPOSAL_BAR =
  'flex min-h-9 shrink-0 flex-wrap items-center gap-2 border-b border-primary-20 bg-primary-10 px-2 py-1 text-xs text-primary-90 sm:gap-3 sm:px-5';

export interface LessonToolbarProps {
  navigation: {
    ui: LessonUi;
    onStage: (stage: Stage) => void;
    onMode: (mode: Mode) => void;
    onView: (view: View) => void;
    onStepFinding: (direction: 1 | -1) => void;
    onAddSelection: () => void;
  };
  review: {
    scope: ReviewScope;
    openFindingCount: number;
    onRun: (scope: ReviewScope) => void;
    onRetry: () => void;
    busy?: boolean;
    startedAt?: number | null;
    progress?: string;
    onCancel?: () => void;
    onUncovered?: () => void;
    error?: string;
    errorDetail?: string | null;
  };
  draft: {
    dirty: boolean;
    clientDirty: boolean;
    onSave: () => void;
    saveDisabled?: boolean;
    saveErrorDetails?: string | null;
  };
  actions: {
    state: LessonState;
    pendingProposalCount: number;
    busy?: boolean;
    proposalAcceptDisabled?: boolean;
    onSettleAll: (verdict: 'accepted' | 'rejected') => void;
    onApprove: () => void;
    onPromote: () => void;
    onRegenerate: (scope: ReviewScope) => void;
    staleExerciseCount: number;
    onRegenerateStaleExercises: () => void;
    onDiscard: () => void;
    hasDraft: boolean;
  };
  audio: {
    missingCount: number;
    staleCount: number;
    onPlan: (selection: 'missing' | 'stale') => void;
  };
}

function uniqueChips(chips: LessonState['statusChips'], draftDirty: boolean) {
  const seen = new Set<string>();
  const values = [
    ...chips,
    {
      label: draftDirty ? 'Uncommitted draft' : 'Committed',
      tone: draftDirty ? ('warning' as const) : ('ok' as const),
    },
  ];
  return values.filter(({ label }) => {
    const key = label.trim().toLocaleLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

export function LessonToolbar(props: LessonToolbarProps) {
  const { navigation, review, draft, actions, audio } = props;
  const { ui } = navigation;
  const { state } = actions;
  const { openFindingCount } = review;
  const { pendingProposalCount } = actions;
  const [elapsed, setElapsed] = React.useState(0);
  React.useEffect(() => {
    if (!review.startedAt) return;
    const start = review.startedAt;
    const update = () => setElapsed(Math.floor((Date.now() - start) / 1000));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [review.startedAt]);
  const localBlockers: LessonGateBlocker[] = [];
  if (draft.clientDirty) localBlockers.push({ kind: 'unsaved_changes', stage: null, target: 'editor' });
  if (!actions.hasDraft && !draft.clientDirty) localBlockers.push({ kind: 'no_draft', stage: null, target: 'draft' });
  const gateBlockers = [...state.approveBlockers, ...localBlockers];
  const gatedStages = state.approvalStages.filter(
    (stage) => stage.status === 'needs_approval' || stage.status === 'stale',
  );
  const nothingToApprove = gatedStages.length === 0;
  const clientBlocked = localBlockers.length > 0;
  const alreadyApproved = actions.hasDraft && nothingToApprove && gateBlockers.length === 0;
  const approveDisabled = clientBlocked || nothingToApprove || Boolean(actions.busy);
  const displayBlockers = gateBlockers.map((blocker) => ({ blocker, label: formatLessonGateBlocker(blocker) }));
  const chips = uniqueChips(state.statusChips, draft.dirty);
  const uncoveredCount = state.reviewCoverage?.uncovered.length ?? 0;
  const hasUnreviewedAcceptance =
    pendingProposalCount === 0 &&
    state.proposals.some((proposal) => isAcceptedProposalUnreviewed(proposal, state.reviewCoverage?.uncovered ?? []));
  const reviewLabel = review.startedAt ? `Reviewing… ${elapsed}s` : 'Run AI review';
  const stageControl = (
    <SegmentedControl
      label="Stage"
      size="sm"
      value={ui.stage}
      onValueChange={navigation.onStage}
      className="[&_[role=radio]]:px-1.5 min-[1440px]:[&_[role=radio]]:px-2.5"
      options={[
        { value: 'lesson', label: 'Lesson' },
        { value: 'plan', label: 'Plan' },
      ]}
    />
  );
  const reviewItems = (compact = false) => (
    <>
      {ui.stage === 'plan' ? (
        <p className="px-2 py-1.5 text-xs text-muted-foreground">
          Plan has no AI review. Switch to Lesson to review blocks.
        </p>
      ) : null}
      {(['lesson', 'section', 'item'] as const).map((scope) => (
        <DropdownMenuItem
          key={scope}
          onSelect={() => review.onRun(scope)}
          disabled={review.busy || ui.stage === 'plan'}
        >
          {scope === 'lesson' ? 'Whole lesson' : scope === 'section' ? 'Current section' : 'Selected block'}
          {review.scope === scope ? <span className="ml-auto text-xs text-muted-foreground">Default</span> : null}
        </DropdownMenuItem>
      ))}
      <DropdownMenuSeparator />
      <DropdownMenuItem
        onSelect={() => review.onUncovered?.()}
        disabled={review.busy || ui.stage === 'plan' || uncoveredCount === 0}
      >
        Review uncovered blocks ({uncoveredCount})
      </DropdownMenuItem>
      {compact ? <DropdownMenuSeparator /> : null}
    </>
  );
  const regenerateItems = () => (
    <>
      <DropdownMenuItem onSelect={() => actions.onRegenerate('lesson')} disabled={actions.busy || ui.stage === 'plan'}>
        Whole lesson…
      </DropdownMenuItem>
      <DropdownMenuItem onSelect={() => actions.onRegenerate('section')} disabled={actions.busy || ui.stage === 'plan'}>
        Current section…
      </DropdownMenuItem>
      <DropdownMenuItem onSelect={() => actions.onRegenerate('item')} disabled={actions.busy || ui.stage === 'plan'}>
        Selected block…
      </DropdownMenuItem>
      {actions.staleExerciseCount > 0 ? (
        <DropdownMenuItem onSelect={actions.onRegenerateStaleExercises} disabled={actions.busy || ui.stage === 'plan'}>
          Stale exercises ({actions.staleExerciseCount})
        </DropdownMenuItem>
      ) : null}
    </>
  );
  const viewControl = (
    <SegmentedControl
      label="Lesson view"
      size="sm"
      value={ui.view}
      onValueChange={navigation.onView}
      className="[&_[role=radio]]:px-1.5 min-[1440px]:[&_[role=radio]]:px-3"
      options={[
        { value: 'author', label: 'Render' },
        { value: 'source', label: 'Source' },
      ]}
    />
  );
  const modeControl = (
    <SegmentedControl
      label="Mode"
      size="sm"
      value={ui.mode}
      onValueChange={navigation.onMode}
      className="[&_[role=radio]]:px-1.5 min-[1440px]:[&_[role=radio]]:px-3"
      options={[
        { value: 'read', label: 'Read' },
        { value: 'review', label: 'Review' },
      ]}
    />
  );
  const findControl = (
    <>
      {/* biome-ignore lint/a11y/useSemanticElements: This groups navigation buttons, not form controls for a fieldset. */}
      <div role="group" aria-label="Open findings" className="flex items-center gap-0.5 min-[1440px]:gap-1.5">
        <Button
          size="icon"
          variant="outline"
          className="size-7 min-[1440px]:size-8"
          aria-label="Previous open finding"
          aria-keyshortcuts="Alt+ArrowUp"
          onClick={() => navigation.onStepFinding(-1)}
        >
          <ChevronUpIcon />
        </Button>
        <span className="min-w-8 text-center text-xs font-semibold text-warning-80 min-[1440px]:min-w-14 min-[1440px]:text-sm">
          {openFindingCount} <span className="hidden min-[1440px]:inline">open</span>
        </span>
        <Button
          size="icon"
          variant="outline"
          className="size-7 min-[1440px]:size-8"
          aria-label="Next open finding"
          aria-keyshortcuts="Alt+ArrowDown"
          onClick={() => navigation.onStepFinding(1)}
        >
          <ChevronDownIcon />
        </Button>
      </div>
    </>
  );
  const approveTitle = alreadyApproved
    ? 'All stages already have owner acceptance and are fresh'
    : clientBlocked || nothingToApprove
      ? `Approval blocked: ${displayBlockers.map(({ label }) => label).join('; ') || 'nothing to approve'}`
      : gateBlockers.length
        ? `Approval needs attention: ${displayBlockers.map(({ label }) => label).join('; ')}`
        : 'Ready to approve';
  const approveButton = (
    <Button
      size="sm"
      title={approveTitle}
      aria-disabled={approveDisabled}
      disabled={approveDisabled}
      onClick={() => {
        if (!approveDisabled) actions.onApprove();
      }}
      className={`${approveDisabled ? 'opacity-55 ' : ''}shrink-0 px-1.5 min-[1440px]:px-3`}
    >
      Approve
    </Button>
  );
  const overflowItems = () => (
    <>
      {audio.missingCount > 0 ? (
        <DropdownMenuItem onSelect={() => audio.onPlan('missing')} disabled={actions.busy}>
          Generate missing audio…
          <span className="ml-auto text-xs text-muted-foreground">{audio.missingCount} clips</span>
        </DropdownMenuItem>
      ) : null}
      {audio.staleCount > 0 ? (
        <DropdownMenuItem onSelect={() => audio.onPlan('stale')} disabled={actions.busy}>
          Regenerate stale audio…
          <span className="ml-auto text-xs text-muted-foreground">{audio.staleCount} clips</span>
        </DropdownMenuItem>
      ) : null}
      {audio.missingCount > 0 || audio.staleCount > 0 ? <DropdownMenuSeparator /> : null}
      <DropdownMenuItem onSelect={actions.onPromote} disabled={actions.busy}>
        Promote…
      </DropdownMenuItem>
      {actions.hasDraft ? (
        <DropdownMenuItem onSelect={actions.onDiscard} disabled={actions.busy}>
          Discard draft
        </DropdownMenuItem>
      ) : null}
    </>
  );
  const actionDropdown = (label: string, children: React.ReactNode) => (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" variant="outline" className="shrink-0 px-1.5 min-[1440px]:px-3">
          {label}
          <ChevronDownIcon className="ml-0.5 size-3.5 min-[1440px]:ml-1" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">{children}</DropdownMenuContent>
    </DropdownMenu>
  );

  return (
    <section aria-label="Lesson tools" className="flex shrink-0 flex-col">
      <div
        role="toolbar"
        aria-label="Lesson actions"
        className="flex min-h-12 shrink-0 flex-wrap items-center gap-0.5 border-b bg-white px-2 py-2 sm:px-3 min-[1024px]:flex-nowrap min-[1024px]:gap-0.5 min-[1440px]:gap-1 max-[360px]:h-12 max-[360px]:min-h-12 max-[360px]:flex-nowrap max-[360px]:justify-between max-[360px]:overflow-hidden max-[360px]:py-1"
      >
        {stageControl}
        <div className="flex shrink-0 items-center gap-0.5 max-[360px]:hidden min-[1440px]:gap-2">
          {modeControl}
          {viewControl}
          {actionDropdown(reviewLabel, reviewItems())}
          {review.startedAt ? (
            <>
              <span className="w-36 truncate text-xs text-muted-foreground" aria-live="polite" title={review.progress}>
                {review.progress ?? 'Preparing review'}
              </span>
              <Button size="sm" variant="outline" onClick={review.onCancel}>
                Cancel
              </Button>
            </>
          ) : null}
          {actionDropdown('Regenerate', regenerateItems())}
          <Button
            size="sm"
            variant="outline"
            className="shrink-0 px-1.5 min-[1440px]:px-3"
            aria-keyshortcuts="Control+L"
            onClick={navigation.onAddSelection}
          >
            Add to chat
            <KbdGroup aria-hidden="true" className="ml-0.5 hidden min-[1440px]:inline-flex">
              <Kbd>Ctrl</Kbd>+<Kbd>L</Kbd>
            </KbdGroup>
          </Button>
          {SeparatorNode()}
          {findControl}
        </div>
        <span className="min-w-0 flex-1 max-[360px]:hidden" />
        <div className="flex shrink-0 items-center gap-0.5 max-[360px]:hidden min-[1440px]:gap-2">
          <Button
            size="sm"
            variant="outline"
            className="shrink-0 px-1.5 min-[1440px]:px-3"
            disabled={!draft.dirty || draft.saveDisabled}
            onClick={draft.onSave}
          >
            Save
          </Button>
          {approveButton}
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="icon"
              variant="outline"
              aria-label="More lesson actions"
              className="ml-0 shrink-0 max-[360px]:ml-0"
            >
              <EllipsisIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="max-h-[80vh] w-64 overflow-y-auto">
            <DropdownMenuItem onSelect={() => navigation.onStage('lesson')}>Lesson</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => navigation.onStage('plan')}>Plan</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => navigation.onMode('read')}>Read mode</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => navigation.onMode('review')}>Review mode</DropdownMenuItem>
            <DropdownMenuSeparator />
            {(['author', 'learner', 'source'] as const).map((view) => (
              <DropdownMenuItem key={view} onSelect={() => navigation.onView(view)}>
                {view === 'author' ? 'Render view' : view === 'learner' ? 'Learner view' : 'Source view'}
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
            {review.startedAt ? (
              <DropdownMenuItem onSelect={() => review.onCancel?.()}>Cancel AI review</DropdownMenuItem>
            ) : null}
            <DropdownMenuItem disabled={review.busy || ui.stage === 'plan'} onSelect={() => review.onRun(review.scope)}>
              {reviewLabel}
            </DropdownMenuItem>
            {reviewItems(true)}
            {regenerateItems()}
            <DropdownMenuItem aria-keyshortcuts="Control+L" onSelect={navigation.onAddSelection}>
              Add to chat · Ctrl+L
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => navigation.onStepFinding(-1)}>Previous open finding</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => navigation.onStepFinding(1)}>
              Next open finding · {openFindingCount} open
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem disabled={!draft.dirty || draft.saveDisabled} onSelect={draft.onSave}>
              Save
            </DropdownMenuItem>
            <DropdownMenuItem
              disabled={approveDisabled}
              onSelect={() => {
                if (!approveDisabled) actions.onApprove();
              }}
            >
              Approve
            </DropdownMenuItem>
            {overflowItems()}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {review.error ? (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 border-b px-2 py-1.5 text-sm text-destructive-90 sm:px-5"
        >
          <span>{review.error}</span>
          <Button size="sm" variant="outline" onClick={review.onRetry}>
            Retry review
          </Button>
          <JobErrorDisclosure detail={review.errorDetail ?? null} className="basis-full text-xs" />
        </div>
      ) : null}
      {draft.saveErrorDetails ? (
        <details role="alert" className="border-b px-2 py-1.5 text-sm text-destructive-90 sm:px-5">
          <summary className="cursor-pointer">Save failed · Details</summary>
          <pre className="mt-1 whitespace-pre-wrap break-words text-xs">{draft.saveErrorDetails}</pre>
        </details>
      ) : null}
      <div className="flex min-h-8 shrink-0 flex-wrap items-center gap-1.5 border-b bg-secondary-0 px-2 py-1 sm:px-5">
        {chips.map((chip) => (
          <StatusChip key={chip.label} tone={chip.tone}>
            {chip.label}
          </StatusChip>
        ))}
        <p id="approve-gate" className="m-0 ml-auto min-w-0 text-xs text-muted-foreground">
          {alreadyApproved ? (
            <span className="font-semibold text-muted-foreground">
              All stages already have owner acceptance and are fresh
            </span>
          ) : gateBlockers.length ? (
            <>
              <span className="font-semibold text-destructive-70">Blocked:</span>{' '}
              {displayBlockers.map(({ blocker, label }, index) => (
                <React.Fragment key={`${blocker.kind}-${blocker.stage ?? 'none'}-${index}`}>
                  {blocker.kind === 'unsaved_changes' ? (
                    <span
                      title="Finish the current edit or wait for the source write to complete"
                      className="font-semibold text-destructive-70"
                    >
                      {label}
                    </span>
                  ) : blocker.kind === 'no_draft' ? (
                    <span className="font-semibold text-destructive-70">{label}</span>
                  ) : (
                    <button
                      type="button"
                      className="cursor-pointer text-left font-semibold text-destructive-70 underline underline-offset-2"
                      title="Open approval details"
                      onClick={() => {
                        if (!draft.clientDirty) actions.onApprove();
                      }}
                    >
                      {label}
                    </button>
                  )}
                  {index < displayBlockers.length - 1 ? ' · ' : ''}
                </React.Fragment>
              ))}
            </>
          ) : (
            <span className="font-semibold text-accent-90">Ready to approve</span>
          )}
        </p>
        <button
          type="button"
          className="shrink-0 text-xs font-semibold text-primary-80 underline underline-offset-2"
          disabled={review.busy || ui.stage === 'plan' || uncoveredCount === 0}
          onClick={review.onUncovered}
        >
          AI review: {state.reviewCoverage?.covered ?? 0}/{state.reviewCoverage?.total ?? 0} blocks covered
        </button>
        {state.promotionBlockers?.length ? (
          <p className="basis-full m-0 text-right text-xs text-destructive-80">
            Promotion blocked · {state.promotionBlockers.map(formatLessonGateBlocker).join(' · ')}
          </p>
        ) : null}
      </div>
      {pendingProposalCount > 0 ? (
        <div className={K_LESSON_TOOLBAR_PROPOSAL_BAR}>
          <span>
            <b>Proposal</b> · {pendingProposalCount} block{pendingProposalCount === 1 ? '' : 's'} pending
          </span>
          {state.proposalCheck?.status === 'failed' ? (
            <span className="text-destructive-90">Check failed · accept blocks individually</span>
          ) : null}
          <span className="flex-1" />
          <Button
            size="sm"
            variant="outline"
            className="h-7"
            disabled={actions.busy || actions.proposalAcceptDisabled}
            onClick={() => actions.onSettleAll('accepted')}
          >
            Accept all
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7"
            disabled={actions.busy}
            onClick={() => actions.onSettleAll('rejected')}
          >
            Reject all
          </Button>
        </div>
      ) : hasUnreviewedAcceptance && review.onUncovered ? (
        <div className={K_LESSON_TOOLBAR_PROPOSAL_BAR}>
          <span>
            <b>Accepted</b> · changes not reviewed yet
          </span>
          <span className="flex-1" />
          <Button
            size="sm"
            variant="outline"
            className="h-7"
            disabled={review.busy || ui.stage === 'plan'}
            title="Run AI review on all uncovered blocks in this lesson"
            onClick={review.onUncovered}
          >
            Review changed blocks
          </Button>
        </div>
      ) : null}
    </section>
  );
}

function SeparatorNode() {
  return (
    <span aria-hidden="true" className="mx-0.5 flex h-5 items-center">
      <Separator orientation="vertical" className="h-full" />
    </span>
  );
}
