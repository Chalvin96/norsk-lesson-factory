/** Entry point: `LessonWorkspace`. */

import * as React from 'react';
import { toast } from 'sonner';
import type { FindingData, LessonPayload, ProposalData, ReviewScope } from '@/api/contracts';
import { ApiError } from '@/api/http';
import { updateLessonRouteState, useUnsavedNavigationWork } from '@/app/router';
import { ChatPane } from '@/chat/chat-pane';
import { useLessonChatWorkflow } from '@/chat/use-lesson-chat-workflow';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import { useWorkbenchJob, WorkbenchJobError } from '@/jobs/use-workbench-job';
import { useLessonAction, useSaveLesson } from '@/lesson/api';
import { LessonAudioPlanDialog, useLessonAudioWorkflow } from '@/lesson/audio/lesson-audio';
import { buildLessonIndexes } from '@/lesson/document/indexes';
import { LessonDocument } from '@/lesson/document/lesson-document';
import { Minimap, type MinimapMark } from '@/lesson/document/minimap';
import { buildRows, buildTargetKey } from '@/lesson/document/rows';
import {
  DecisionReasons,
  decisionCommentMaxChars,
  type FindingReason,
  findingReasons,
  type KeepDecision,
} from '@/lesson/review/decision-reasons';
import { useLessonReviewWorkflow } from '@/lesson/review/use-lesson-review-workflow';
import { LessonToolbar } from '@/lesson/toolbar/lesson-toolbar';
import { buildInitialLessonLocalState, type LessonRouteState, lessonReducer, type Stage, type View } from './lesson-ui';
import { type LifecycleDialog, LifecycleDialogs } from './lifecycle/lifecycle-dialogs';

type PendingSwitch = { kind: 'row'; rowId: string } | { kind: 'view'; view: View } | { kind: 'stage'; stage: Stage };

export function LessonWorkspace({
  lesson,
  routeState,
  defaultReviewScope,
}: {
  lesson: LessonPayload;
  routeState: LessonRouteState;
  defaultReviewScope?: ReviewScope;
}) {
  const [localUi, dispatch] = React.useReducer(lessonReducer, undefined, buildInitialLessonLocalState);
  const ui = { ...routeState, ...localUi };
  const save = useSaveLesson(lesson.lessonId);
  const action = useLessonAction(lesson.lessonId);
  const review = useWorkbenchJob(['lesson', lesson.lessonId]);
  const audio = useLessonAudioWorkflow(lesson.lessonId);
  const [lifecycleDialog, setLifecycleDialog] = React.useState<LifecycleDialog>(null);
  const [regenerationSelection, setRegenerationSelection] = React.useState<{
    scope: ReviewScope;
    section: string | null;
    item: string | null;
  }>({ scope: 'section', section: null, item: null });
  const [resolvingFindings, setResolvingFindings] = React.useState<FindingData[] | null>(null);
  const [resolutionNote, setResolutionNote] = React.useState('');
  const [resolutionReason, setResolutionReason] = React.useState<FindingReason>('intended');
  const resolutionOrigin = React.useRef<HTMLElement | null>(null);
  const lastFocusedRow = React.useRef<string | null>(null);
  const runAction = React.useCallback(
    (route: string) => action.mutate({ route }, { onError: (error) => toast.error(error.message) }),
    [action.mutate],
  );
  const requestAudioPlan = React.useCallback(
    (selection: 'missing' | 'stale' | 'missing_and_stale', blockKey?: string) => {
      void audio.requestPlan(selection, blockKey).catch((error: unknown) => {
        toast.error(error instanceof Error ? error.message : 'Could not plan lesson audio.');
      });
    },
    [audio.requestPlan],
  );

  const [sourceBusy, setSourceBusy] = React.useState(false);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const rowElements = React.useRef(new Map<string, HTMLDivElement>());
  const rowRefCallbacks = React.useRef(new Map<string, (element: HTMLDivElement | null) => void>());
  const rows = React.useMemo(() => buildRows(lesson.elements), [lesson.elements]);
  const [openRowDirty, setOpenRowDirty] = React.useState(false);
  const [pendingSwitch, setPendingSwitch] = React.useState<PendingSwitch | null>(null);
  const returnFocus = React.useRef<HTMLElement | null>(null);
  const lastEditorField = React.useRef<HTMLElement | null>(null);
  const openRow = React.useCallback((rowId: string | null) => {
    setOpenRowDirty(false);
    dispatch({ type: 'open-row', rowId });
  }, []);
  const discardOpenRow = React.useCallback(() => openRow(null), [openRow]);
  useUnsavedNavigationWork(openRowDirty, discardOpenRow);
  const leavesEditor = React.useCallback(
    (target: PendingSwitch) =>
      Boolean(ui.openRowId) &&
      (target.kind === 'row'
        ? target.rowId !== ui.openRowId
        : target.kind === 'view'
          ? target.view !== 'author'
          : target.stage === 'plan'),
    [ui.openRowId],
  );
  const applySwitch = React.useCallback(
    (target: PendingSwitch) => {
      if (target.kind === 'row') {
        openRow(target.rowId);
        return;
      }
      if (leavesEditor(target)) openRow(null);
      updateLessonRouteState(target.kind === 'view' ? { view: target.view } : { stage: target.stage });
    },
    [leavesEditor, openRow],
  );
  const requestSwitch = React.useCallback(
    (target: PendingSwitch) => {
      if (openRowDirty && leavesEditor(target)) {
        returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        setPendingSwitch(target);
      } else {
        applySwitch(target);
      }
    },
    [applySwitch, leavesEditor, openRowDirty],
  );
  const restoreFocusAfterDialog = (event: Event) => {
    event.preventDefault();
    requestAnimationFrame(() => {
      const remembered = lastEditorField.current?.isConnected ? lastEditorField.current : null;
      const editorField =
        remembered ?? document.querySelector<HTMLElement>('[aria-label="Edit block"] :is(input, textarea)');
      const fallback = returnFocus.current?.isConnected
        ? returnFocus.current
        : document.getElementById('workbench-main');
      (editorField ?? fallback)?.focus();
    });
  };

  const indexes = React.useMemo(
    () =>
      buildLessonIndexes({
        findings: lesson.state.findings,
        proposals: lesson.state.proposals,
        audioClips: lesson.audio.clips,
        exerciseStaleness: lesson.state.exerciseStaleness ?? [],
        blocks: lesson.blocks,
      }),
    [lesson.audio.clips, lesson.blocks, lesson.state.exerciseStaleness, lesson.state.findings, lesson.state.proposals],
  );

  const openFindings = React.useMemo(() => {
    const order = new Map(rows.map((row, index) => [row.targetKey, index]));
    return lesson.state.findings
      .filter((finding) => finding.status === 'open')
      .sort((a, b) => (order.get(buildTargetKey(a.target)) ?? 0) - (order.get(buildTargetKey(b.target)) ?? 0));
  }, [lesson.state.findings, rows]);
  const exerciseStaleness = lesson.state.exerciseStaleness ?? [];
  const staleExerciseKeys = exerciseStaleness.flatMap((item) => (item.status === 'stale' ? [item.exerciseKey] : []));
  const pendingProposals = lesson.state.proposals.filter(
    (proposal) => proposal.status === 'pending' || proposal.status === 'conflicted',
  );
  const pendingProposalIds = [...new Set(pendingProposals.map((proposal) => proposal.proposalId))];
  const openProposal = (proposalId: string) => {
    const target = [...document.querySelectorAll<HTMLElement>('[data-proposal-id]')].find(
      (element) => element.dataset.proposalId === proposalId,
    );
    target?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    target?.focus({ preventScroll: true });
  };
  const stepProposal = (direction: 1 | -1) => {
    if (!pendingProposalIds.length) return;
    const current = [...document.querySelectorAll<HTMLElement>('[data-proposal-id]')].find(
      (element) => element === document.activeElement,
    )?.dataset.proposalId;
    const index = pendingProposalIds.indexOf(current ?? '');
    const next =
      index < 0
        ? direction > 0
          ? 0
          : pendingProposalIds.length - 1
        : (index + direction + pendingProposalIds.length) % pendingProposalIds.length;
    const proposalId = pendingProposalIds[next];
    if (proposalId) openProposal(proposalId);
  };

  const rowRef = React.useCallback((rowId: string) => {
    const existing = rowRefCallbacks.current.get(rowId);
    if (existing) return existing;
    const callback = (element: HTMLDivElement | null) => {
      if (element) rowElements.current.set(rowId, element);
      else rowElements.current.delete(rowId);
    };
    rowRefCallbacks.current.set(rowId, callback);
    return callback;
  }, []);

  const closeRow = React.useCallback(() => {
    const rowId = ui.openRowId;
    openRow(null);
    if (rowId) {
      requestAnimationFrame(() =>
        rowElements.current.get(rowId)?.querySelector<HTMLButtonElement>('[aria-label^="Edit "]')?.focus(),
      );
    }
  }, [openRow, ui.openRowId]);

  const onOpenDocumentRow = React.useCallback(
    (rowId: string) => requestSwitch({ kind: 'row', rowId }),
    [requestSwitch],
  );
  const onEditComplete = React.useCallback((rowId: string) => dispatch({ type: 'mark-edited', rowId }), []);

  const stepFinding = React.useCallback(
    (direction: 1 | -1) => {
      if (!openFindings.length) {
        toast('No open findings.');
        return;
      }
      const index = openFindings.findIndex((finding) => finding.findingId === ui.currentFindingId);
      const next =
        index < 0
          ? openFindings[direction > 0 ? 0 : openFindings.length - 1]
          : openFindings[(index + direction + openFindings.length) % openFindings.length];
      updateLessonRouteState({ mode: 'review' });
      dispatch({ type: 'focus-finding', findingId: next.findingId });
      requestAnimationFrame(() => {
        const note = document.getElementById(`finding-${next.findingId}`);
        note?.closest('[data-row-kind]')?.scrollIntoView({ block: 'center' });
        note?.focus({ preventScroll: true });
      });
    },
    [openFindings, ui.currentFindingId],
  );

  const marks: MinimapMark[] = React.useMemo(
    () =>
      ui.stage === 'plan' || ui.view === 'source' || ui.view === 'learner'
        ? []
        : rows.map((row) => {
            return {
              rowId: row.rowId,
              kind: row.kind,
              flag: ui.mode === 'review' ? (indexes.flagByTarget.get(row.targetKey) ?? null) : null,
              hasProposal: indexes.pendingProposalByTarget.has(row.targetKey),
              label: row.kind === 'heading' ? row.sectionLabel : null,
            };
          }),
    [indexes.flagByTarget, indexes.pendingProposalByTarget, rows, ui.mode, ui.stage, ui.view],
  );

  const settle = React.useCallback(
    (proposal: ProposalData, verdict: 'accepted' | 'rejected') =>
      runAction(
        `proposals/${encodeURIComponent(proposal.proposalId)}/blocks/${encodeURIComponent(proposal.key)}/${verdict === 'accepted' ? 'accept' : 'reject'}`,
      ),
    [runAction],
  );
  const keepExercise = React.useCallback(
    (exerciseKey: string, decision: KeepDecision) =>
      action.mutate(
        { route: `exercises/${encodeURIComponent(exerciseKey)}/keep`, body: decision },
        { onError: (error) => toast.error(error.message) },
      ),
    [action.mutate],
  );
  const keepProposalExercise = React.useCallback(
    (proposal: ProposalData, decision: KeepDecision) =>
      action.mutate(
        {
          route: `proposals/${encodeURIComponent(proposal.proposalId)}/blocks/${encodeURIComponent(proposal.key)}/keep`,
          body: decision,
        },
        { onError: (error) => toast.error(error.message) },
      ),
    [action.mutate],
  );
  const currentSection = React.useCallback(() => {
    const top = scrollRef.current?.getBoundingClientRect().top ?? 0;
    const nearest = rows.find(
      (row) => (rowElements.current.get(row.rowId)?.getBoundingClientRect().bottom ?? -1) > top,
    );
    return nearest?.kind === 'exercise'
      ? rows
          .slice(0, rows.indexOf(nearest))
          .reverse()
          .find((row) => row.kind === 'heading')
      : nearest;
  }, [rows]);
  const currentSectionKey = React.useCallback(() => currentSection()?.targetKey, [currentSection]);
  const mutateReview = React.useCallback(
    (request: { route: string; body: object }) => review.mutate(request),
    [review.mutate],
  );
  const reviewWorkflow = useLessonReviewWorkflow({
    lessonId: lesson.lessonId,
    rows,
    selectedRowId: ui.openRowId ?? lastFocusedRow.current,
    currentSectionKey,
    defaultReviewScope,
    mutate: mutateReview,
  });
  const { reviewScope, runReview, runReviewUncovered } = reviewWorkflow;
  const chatWorkflow = useLessonChatWorkflow({
    lessonId: lesson.lessonId,
    chatHistory: lesson.state.chat,
    activeTurn: lesson.state.activeTurn,
    rows,
    currentSectionKey,
  });
  const {
    chat,
    pendingTurn,
    composerChips,
    addSelection,
    askChat,
    onFix,
    onExplain,
    addContextChip,
    removeContextChip,
    sendChat,
  } = chatWorkflow;

  React.useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'l') {
        event.preventDefault();
        addSelection();
      } else if (event.altKey && (event.key === 'ArrowUp' || event.key === 'ArrowDown')) {
        event.preventDefault();
        stepFinding(event.key === 'ArrowUp' ? -1 : 1);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [addSelection, stepFinding]);
  const beginResolution = React.useCallback((findings: FindingData[]) => {
    const first = findings[0];
    if (!first) return;
    resolutionOrigin.current = document.getElementById(`finding-${first.findingId}`);
    setResolutionNote('');
    setResolutionReason('intended');
    setResolvingFindings(findings);
  }, []);
  const resolveFinding = React.useCallback((finding: FindingData) => beginResolution([finding]), [beginResolution]);
  const generateAudioForRow = React.useCallback(
    (rowKey: string) => requestAudioPlan('missing_and_stale', rowKey),
    [requestAudioPlan],
  );

  return (
    <>
      <LessonToolbar
        navigation={{
          ui,
          onStage: (stage) => requestSwitch({ kind: 'stage', stage }),
          onMode: (mode) => updateLessonRouteState({ mode }),
          onView: (view) => requestSwitch({ kind: 'view', view }),
          onStepFinding: stepFinding,
          onAddSelection: addSelection,
        }}
        review={{
          scope: reviewScope,
          openFindingCount: openFindings.length,
          onRun: runReview,
          onRetry: review.retry,
          busy: review.isPending || sourceBusy || openRowDirty,
          startedAt: review.isPending ? review.startedAt : null,
          progress: review.steps.at(-1),
          onCancel: () =>
            void review
              .cancel()
              .catch((error: unknown) =>
                toast.error(error instanceof Error ? error.message : 'Could not cancel the review.'),
              ),
          onUncovered: runReviewUncovered,
          error: review.error?.message,
          errorDetail: review.error instanceof WorkbenchJobError ? review.error.detail : null,
        }}
        draft={{
          dirty: lesson.draft.dirty,
          clientDirty: openRowDirty || sourceBusy,
          saveDisabled: sourceBusy || save.isPending || Boolean(ui.openRowId),
          saveErrorDetails: save.error instanceof ApiError ? save.error.details : null,
          onSave: () =>
            save.mutate(undefined, {
              onSuccess: (result) => {
                toast.success(`Saved ${result.commit.slice(0, 7)}`);
              },
              onError: (error) => toast.error(error.message),
            }),
        }}
        actions={{
          state: lesson.state,
          pendingProposalCount: pendingProposals.length,
          busy: action.isPending || audio.planBusy || audio.synthesis.isPending || sourceBusy || Boolean(ui.openRowId),
          proposalAcceptDisabled:
            lesson.state.proposalCheck?.status === 'failed' ||
            pendingProposals.some((proposal) => proposal.status === 'conflicted'),
          onSettleAll: (verdict) => {
            const first = pendingProposals[0];
            if (first)
              runAction(
                `proposals/${encodeURIComponent(first.proposalId)}/${verdict === 'accepted' ? 'accept' : 'reject'}`,
              );
          },
          onApprove: () => {
            if (!openRowDirty && !sourceBusy) setLifecycleDialog('approve');
          },
          onPromote: () => setLifecycleDialog('promote'),
          onDiscard: () => setLifecycleDialog('discard'),
          hasDraft: lesson.draft.exists,
          onRegenerate: (scope) => {
            const selected = rows.find((row) => row.rowId === (ui.openRowId ?? lastFocusedRow.current));
            setRegenerationSelection({
              scope,
              section: currentSection()?.targetKey.split('#')[0] ?? null,
              item: selected?.targetKey ?? null,
            });
            setLifecycleDialog('regenerate');
          },
          staleExerciseCount: staleExerciseKeys.length,
          onRegenerateStaleExercises: () => setLifecycleDialog('regenerate-exercises'),
        }}
        audio={{
          missingCount: lesson.audio.missingCount,
          staleCount: lesson.audio.staleCount,
          onPlan: (selection) => requestAudioPlan(selection),
        }}
      />
      <LifecycleDialogs
        lesson={lesson}
        dialog={lifecycleDialog}
        onDialog={setLifecycleDialog}
        regenerationScope={regenerationSelection.scope}
        sectionKey={regenerationSelection.section}
        itemKey={regenerationSelection.item}
        exerciseKeys={staleExerciseKeys}
        onReviewUncovered={runReviewUncovered}
        busy={action.isPending || save.isPending || sourceBusy || Boolean(ui.openRowId)}
      />
      <LessonAudioPlanDialog
        plan={audio.plan}
        onClose={() => audio.setPlan(null)}
        onConfirm={audio.startSynthesis}
        busy={audio.synthesis.isPending}
        progress={audio.synthesis.steps}
        completed={audio.synthesis.isSuccess}
        error={audio.synthesis.error?.message ?? null}
        errorDetail={audio.synthesis.error instanceof WorkbenchJobError ? audio.synthesis.error.detail : null}
      />
      <div className="grid min-h-96 flex-1 grid-cols-1 grid-rows-[minmax(12rem,1fr)_minmax(12rem,30vh)] md:min-h-0 md:grid-cols-[minmax(0,1fr)_clamp(20rem,24vw,26rem)] md:grid-rows-[minmax(0,1fr)]">
        <main
          id="workbench-main"
          tabIndex={-1}
          onPointerDown={(event) => {
            if (event.target instanceof HTMLElement)
              lastFocusedRow.current = event.target.closest('[data-row-kind]')?.id ?? lastFocusedRow.current;
          }}
          onFocus={(event) => {
            lastFocusedRow.current = event.target.closest('[data-row-kind]')?.id ?? lastFocusedRow.current;
            if (event.target.closest('[aria-label="Edit block"]') && event.target.matches('input, textarea'))
              lastEditorField.current = event.target;
          }}
          aria-labelledby="lesson-title"
          className="flex min-h-0 min-w-0 border-r bg-background focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
        >
          <div id="lesson-document-scroll" ref={scrollRef} className="min-w-0 flex-1 overflow-y-auto">
            <LessonDocument
              lesson={lesson}
              rows={rows}
              indexes={indexes}
              ui={ui}
              rowRef={rowRef}
              onOpenRow={onOpenDocumentRow}
              onCloseRow={closeRow}
              onDirtyChange={setOpenRowDirty}
              onSourceBusyChange={setSourceBusy}
              onEdited={onEditComplete}
              actionsBusy={action.isPending || sourceBusy || Boolean(ui.openRowId)}
              onResolve={resolveFinding}
              onResolveSimilar={beginResolution}
              onFix={onFix}
              onExplain={onExplain}
              explanationBusy={chat.isPending}
              agentBusy={chat.isPending}
              onAskChat={askChat}
              onSettle={settle}
              onKeepExercise={keepExercise}
              onKeepProposal={keepProposalExercise}
              onGenerateAudio={generateAudioForRow}
              onReviewChanged={runReviewUncovered}
            />
          </div>
          {marks.length ? <Minimap scrollRef={scrollRef} rowElements={rowElements} marks={marks} /> : null}
        </main>
        <Dialog open={pendingSwitch !== null} onOpenChange={(open) => !open && setPendingSwitch(null)}>
          <DialogContent onCloseAutoFocus={restoreFocusAfterDialog}>
            <DialogHeader>
              <DialogTitle>Discard changes to the open block?</DialogTitle>
              <DialogDescription>
                The open block has edits that are not done yet. Keep editing, or discard them and{' '}
                {pendingSwitch?.kind === 'row' ? 'open the other block' : 'switch view'}.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter>
              <Button variant="dialogSecondary" onClick={() => setPendingSwitch(null)}>
                Keep editing
              </Button>
              <Button
                variant="destructive"
                onClick={() => {
                  if (pendingSwitch) applySwitch(pendingSwitch);
                  setPendingSwitch(null);
                }}
              >
                Discard and continue
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <Dialog open={resolvingFindings !== null} onOpenChange={(open) => !open && setResolvingFindings(null)}>
          <DialogContent
            className="max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] overflow-y-auto rounded-2xl sm:max-w-[520px]"
            onCloseAutoFocus={(event) => {
              event.preventDefault();
              resolutionOrigin.current?.focus();
            }}
          >
            <DialogHeader>
              <DialogTitle>
                {resolvingFindings?.length === 1 ? 'Resolve finding' : 'Resolve similar findings'}
              </DialogTitle>
              <DialogDescription>
                Leave this as it is? Your reason is saved with the approval evidence, and the next AI review is told
                about your decision.
              </DialogDescription>
            </DialogHeader>
            {resolvingFindings?.map((finding) => (
              <div key={finding.findingId} className="min-w-0 rounded-xl bg-secondary-10 p-3.5 text-sm break-words">
                <p className="mb-1 text-xs text-secondary-80">
                  {finding.label} · {finding.level}
                </p>
                <p>{finding.message || finding.explanation}</p>
              </div>
            ))}
            <DecisionReasons options={findingReasons} value={resolutionReason} onChange={setResolutionReason} />
            <label htmlFor="resolution-comment" className="text-sm font-semibold">
              Comment (optional)
            </label>
            <Textarea
              id="resolution-comment"
              maxLength={decisionCommentMaxChars}
              aria-describedby="resolution-comment-count"
              placeholder="Anything worth remembering about this decision"
              value={resolutionNote}
              onChange={(event) => setResolutionNote(event.target.value)}
            />
            <p id="resolution-comment-count" className="text-xs text-secondary-80">
              {resolutionNote.length} / {decisionCommentMaxChars}
            </p>
            <DialogFooter>
              <Button variant="dialogSecondary" onClick={() => setResolvingFindings(null)} disabled={action.isPending}>
                Cancel
              </Button>
              <Button
                disabled={action.isPending}
                onClick={() => {
                  const selected = resolvingFindings;
                  const first = selected?.[0];
                  if (!first || !selected) return;
                  action.mutate(
                    {
                      route: `findings/${encodeURIComponent(first.findingId)}/resolve`,
                      body: {
                        finding_ids: selected.map((finding) => finding.findingId),
                        reason: resolutionReason,
                        comment: resolutionNote.trim(),
                      },
                    },
                    { onSuccess: () => setResolvingFindings(null), onError: (error) => toast.error(error.message) },
                  );
                }}
              >
                {resolvingFindings?.length === 1
                  ? 'Resolve finding'
                  : `Resolve ${resolvingFindings?.length ?? 0} findings`}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
        <ChatPane
          contextOptions={chatWorkflow.contextOptions}
          turns={pendingTurn ? [...lesson.state.chat, pendingTurn] : lesson.state.chat}
          chips={composerChips}
          pendingSince={chat.startedAt}
          partialText={chat.partial}
          error={chat.error?.message}
          errorDetail={chat.error instanceof WorkbenchJobError ? chat.error.detail : null}
          onRetry={chat.retry}
          onRetryTurn={chat.retryTurn}
          onCancel={() =>
            void chat
              .cancel()
              .catch((error: unknown) =>
                toast.error(error instanceof Error ? error.message : 'Could not cancel the chat turn.'),
              )
          }
          onNewChat={() =>
            void chat
              .newChat()
              .catch((error: unknown) =>
                toast.error(error instanceof Error ? error.message : 'Could not start a new chat.'),
              )
          }
          proposalIds={pendingProposalIds}
          onOpenProposal={openProposal}
          onStepProposal={stepProposal}
          inputRef={chatWorkflow.inputRef}
          onAddChip={addContextChip}
          onRemoveChip={removeContextChip}
          onSend={sendChat}
        />
      </div>
    </>
  );
}
