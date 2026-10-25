import { GripVerticalIcon, MoreHorizontalIcon } from 'lucide-react';
import * as React from 'react';
import { toast } from 'sonner';
import type { CurriculumEditPreview, CurriculumLesson, CurriculumPayload } from '@/api/contracts';
import { Link, lessonHref, useUnsavedNavigationWork } from '@/app/router';
import { ChatPane } from '@/chat/chat-pane';
import type { ChatTurnData, ContextChipData } from '@/chat/types';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { Separator } from '@/components/ui/separator';
import { PaneHeader, StatusChip } from '@/components/workbench';
import { PageStatus } from '@/components/workbench/page-status';
import { useCurriculum, useCurriculumChecks, useSaveCurriculum } from '@/curriculum/api';
import type { CurriculumEdit } from '@/curriculum/types';
import { useNewLessonJobs } from '@/jobs/new-lesson-jobs';
import { useWorkbenchJob, WorkbenchJobError } from '@/jobs/use-workbench-job';
import { cn } from '@/lib/utils';
import { EntryForm } from './entry-form';
import { multipleLessonIds, nonEmptyLessonIds } from './form-values';
import { NewLessonSession } from './new-lesson-session';

const CurriculumEditDialog = React.lazy(() =>
  import('./dialogs/curriculum-edit-dialog').then(({ CurriculumEditDialog }) => ({ default: CurriculumEditDialog })),
);
const CurriculumReviewDialog = React.lazy(() =>
  import('./dialogs/curriculum-review-dialog').then(({ CurriculumReviewDialog }) => ({
    default: CurriculumReviewDialog,
  })),
);
export function moveItem<T>(items: readonly T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= items.length || to >= items.length) return [...items];
  const next = [...items];
  const [moved] = next.splice(from, 1);
  next.splice(to, 0, moved);
  return next;
}

function reconcileOrder(current: string[], serverOrder: string[]): string[] {
  const serverIds = new Set(serverOrder);
  const currentIds = new Set(current);
  const reconciled = [
    ...current.filter((lessonId) => serverIds.has(lessonId)),
    ...serverOrder.filter((lessonId) => !currentIds.has(lessonId)),
  ];
  return reconciled.every((lessonId, index) => current[index] === lessonId) && reconciled.length === current.length
    ? current
    : reconciled;
}

function EntryDetail({
  lesson,
  data,
  onPreview,
}: {
  lesson: CurriculumLesson | undefined;
  data: CurriculumPayload;
  onPreview: (edit: CurriculumEdit) => void;
}) {
  const [editing, setEditing] = React.useState(false);
  if (!lesson) return <p className="p-4 text-sm text-muted-foreground">Select an entry to see its details.</p>;
  const entry = data.entries[lesson.lessonId];
  return (
    <div className="overflow-y-auto px-4 py-5">
      <span className="text-xs font-semibold tracking-[0.16em] text-muted-foreground uppercase">
        {lesson.source === 'missing' ? 'Not drafted' : lesson.source === 'draft' ? 'Draft' : 'Canonical'}
      </span>
      <h3 id="entry-title" className="mt-1.5 mb-4 text-2xl leading-tight">
        {lesson.title}
      </h3>
      {editing && entry ? (
        <EntryForm lesson={lesson} entry={entry} onPreview={onPreview} onClose={() => setEditing(false)} />
      ) : (
        <>
          <Button size="sm" variant="outline" className="mb-4" onClick={() => setEditing(true)}>
            Edit entry
          </Button>
          <p className="mb-4 text-sm">{entry?.learnerOutcome}</p>
          <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted-foreground">Lesson id</dt>
            <dd className="m-0 break-all">
              <code className="rounded bg-secondary-10 px-1.5">{lesson.lessonId}</code>
            </dd>
            <dt className="text-muted-foreground">Level</dt>
            <dd className="m-0">{lesson.cefrLevel}</dd>
            <dt className="text-muted-foreground">Depends on</dt>
            <dd className="m-0">{entry?.dependsOn.length ? entry.dependsOn.join(', ') : 'Nothing earlier'}</dd>
          </dl>
        </>
      )}
      <Separator className="my-5" />
      <span className="text-[0.68rem] font-semibold tracking-[0.16em] text-secondary-80 uppercase">Plan findings</span>
      {entry?.findings.length ? (
        <ul className="mt-2 grid gap-2 text-sm text-destructive-90">
          {entry.findings.map((finding, index) => (
            <li key={index}>{finding.explanation}</li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-sm text-muted-foreground">No located plan findings.</p>
      )}
      <div className="mt-6 flex gap-2">
        <Button asChild size="sm" disabled={lesson.source === 'missing'}>
          {lesson.source === 'missing' ? (
            <span>No draft yet</span>
          ) : (
            <Link href={lessonHref(lesson.lessonId)}>Open lesson</Link>
          )}
        </Button>
      </div>
    </div>
  );
}

function CurriculumWorkspace({ data }: { data: CurriculumPayload }) {
  const [draftOrderIds, setDraftOrderIds] = React.useState<string[] | null>(null);
  const [selectedId, setSelectedId] = React.useState(data.lessons[0]?.lessonId);
  const [selectedIds, setSelectedIds] = React.useState<string[]>([]);
  const [stagedEdits, setStagedEdits] = React.useState<CurriculumEdit[]>([]);
  const [stagedPreview, setStagedPreview] = React.useState<CurriculumEditPreview | null>(null);
  const [dialogEdit, setDialogEdit] = React.useState<CurriculumEdit | null>(null);
  const [dialogOpen, setDialogOpen] = React.useState(false);
  const newLessonJobs = useNewLessonJobs();
  const [reviewEdits, setReviewEdits] = React.useState<CurriculumEdit[]>([]);
  const [reviewOpen, setReviewOpen] = React.useState(false);
  const save = useSaveCurriculum();
  const serverOrderIds = React.useMemo(() => data.lessons.map((lesson) => lesson.lessonId), [data.lessons]);
  const baseOrderIds = React.useMemo(
    () => stagedPreview?.entries.map((entry) => entry.catalogId) ?? serverOrderIds,
    [serverOrderIds, stagedPreview],
  );
  const orderIds = draftOrderIds ?? baseOrderIds;
  React.useEffect(() => {
    if (stagedPreview) return;
    setDraftOrderIds((current) => (current === null ? null : reconcileOrder(current, serverOrderIds)));
  }, [serverOrderIds, stagedPreview]);
  React.useEffect(() => {
    setSelectedId((current) => (current && baseOrderIds.includes(current) ? current : baseOrderIds[0]));
    setSelectedIds((current) => current.filter((lessonId) => baseOrderIds.includes(lessonId)));
  }, [baseOrderIds]);
  const orderChanged =
    orderIds.length !== baseOrderIds.length || orderIds.some((lessonId, index) => baseOrderIds[index] !== lessonId);
  const moved = orderChanged ? orderIds.filter((lessonId, index) => baseOrderIds[index] !== lessonId) : [];
  const [dragging, setDragging] = React.useState<string | null>(null);
  const [pendingTurn, setPendingTurn] = React.useState<ChatTurnData | null>(null);
  const chat = useWorkbenchJob(['curriculum'], data.chat);
  React.useEffect(() => {
    if (chat.isSuccess) setPendingTurn(null);
  }, [chat.isSuccess]);
  const checkCounts = useCurriculumChecks().data?.counts;
  const [chips, setChips] = React.useState<ContextChipData[]>([]);
  const buttonRefs = React.useRef(new Map<string, HTMLButtonElement>());
  const unsaved = orderChanged || stagedEdits.length > 0;
  useUnsavedNavigationWork(unsaved);
  const pendingEdits = React.useMemo(() => {
    const orderedLessonIds = nonEmptyLessonIds(orderIds);
    return orderChanged && orderedLessonIds
      ? [...stagedEdits, { kind: 'reorder' as const, lessonIds: orderedLessonIds }]
      : stagedEdits;
  }, [orderChanged, orderIds, stagedEdits]);
  const serverLessons = React.useMemo(
    () => new Map(data.lessons.map((lesson) => [lesson.lessonId, lesson])),
    [data.lessons],
  );
  const previewEntries = React.useMemo(
    () => new Map(stagedPreview?.entries.map((entry) => [entry.catalogId, entry]) ?? []),
    [stagedPreview],
  );
  const workingLessons = React.useMemo(
    () =>
      orderIds.flatMap((lessonId, sequenceIndex) => {
        const serverLesson = serverLessons.get(lessonId);
        const previewEntry = previewEntries.get(lessonId);
        if (!serverLesson && !previewEntry) return [];
        return [
          {
            lessonId,
            title: previewEntry?.title ?? serverLesson?.title ?? lessonId,
            cefrLevel: previewEntry?.cefrLevel ?? serverLesson?.cefrLevel ?? 'A1',
            sequenceIndex,
            source: serverLesson?.source ?? 'missing',
          },
        ];
      }),
    [orderIds, previewEntries, serverLessons],
  );
  const workingEntries = stagedPreview
    ? Object.fromEntries(
        stagedPreview.entries.map((entry) => {
          const affected = stagedPreview.affectedLessons.some(
            (item) => item.lessonId === entry.catalogId && item.effect === 'needs_replan',
          );
          const dependencyFindings = stagedPreview.dependencyFindings
            .filter((item) => item.lessonId === entry.catalogId)
            .map((finding) => ({
              lessonId: finding.lessonId,
              code: finding.code,
              explanation: finding.explanation,
              artifact: 'content/curriculum/plan.yaml',
            }));
          return [
            entry.catalogId,
            {
              learnerOutcome: entry.learnerOutcome,
              cefrLevel: entry.cefrLevel,
              findings: dependencyFindings,
              statusChips: [
                ...(data.entries[entry.catalogId]?.statusChips ?? []),
                ...(affected ? [{ label: 'Needs re-plan', tone: 'warning' as const }] : []),
              ],
              dependsOn: entry.prerequisites,
              helpfulPrerequisites: entry.helpfulPrerequisites,
            },
          ];
        }),
      )
    : data.entries;
  const draftCount = workingLessons.filter((lesson) => lesson.source === 'draft').length;

  const openEdit = (edit: CurriculumEdit) => {
    setDialogEdit(edit);
    setDialogOpen(true);
  };

  const acceptEdit = (edit: CurriculumEdit, preview: CurriculumEditPreview) => {
    setStagedEdits([...pendingEdits, edit]);
    setStagedPreview(preview);
    setDraftOrderIds(preview.entries.map((entry) => entry.catalogId));
    setSelectedId(
      preview.entries.find((entry) => entry.catalogId === selectedId)?.catalogId ?? preview.entries[0]?.catalogId,
    );
    setSelectedIds([]);
    setDialogOpen(false);
  };

  const openMerge = () => {
    const selected = workingLessons.filter((lesson) => selectedIds.includes(lesson.lessonId));
    const lessonIds = multipleLessonIds(selected.map((lesson) => lesson.lessonId));
    if (!lessonIds) return;
    const entries = selected.map((lesson) => workingEntries[lesson.lessonId]);
    openEdit({
      kind: 'merge',
      lessonIds,
      title: selected.map((lesson) => lesson.title).join(' and '),
      learnerOutcome: entries
        .map((entry, index) => `${selected[index].title}: ${entry?.learnerOutcome ?? ''}`)
        .filter(Boolean)
        .join(' '),
    });
  };

  const openSplit = (lesson: CurriculumLesson) => {
    const entry = workingEntries[lesson.lessonId];
    openEdit({
      kind: 'split',
      lessonId: lesson.lessonId,
      parts: [
        { catalogId: lesson.lessonId, title: lesson.title, learnerOutcome: entry?.learnerOutcome ?? '' },
        { catalogId: `${lesson.lessonId}_part_2`, title: '', learnerOutcome: '' },
      ],
    });
  };

  const openRemove = () => {
    const ids = workingLessons
      .filter((lesson) => selectedIds.includes(lesson.lessonId))
      .map((lesson) => lesson.lessonId);
    const lessonIds = nonEmptyLessonIds(ids);
    if (!lessonIds) return;
    openEdit({ kind: 'remove', lessonIds, deleteNeverPromotedDrafts: false });
  };

  const openAdd = () =>
    openEdit({
      kind: 'add',
      entry: {
        catalogId: 'new_curriculum_entry',
        title: '',
        learnerOutcome: '',
        cefrLevel: 'A1',
        prerequisites: [],
        helpfulPrerequisites: [],
      },
      afterLessonId: selectedId ?? null,
    });

  const beginReview = () => {
    const orderedLessonIds = nonEmptyLessonIds(orderIds);
    setReviewEdits(
      pendingEdits.length ? pendingEdits : orderedLessonIds ? [{ kind: 'reorder', lessonIds: orderedLessonIds }] : [],
    );
    setReviewOpen(true);
  };

  const savePreviewedEdits = (input: { reviewDigest: string | null }) => {
    save.mutate(
      { edits: reviewEdits, ...input },
      {
        onSuccess: (result) => {
          setDraftOrderIds(null);
          setStagedEdits([]);
          setStagedPreview(null);
          setSelectedIds([]);
          setReviewOpen(false);
          toast(`Curriculum saved · ${result.commit.slice(0, 7)}`);
        },
      },
    );
  };

  const move = (lessonId: string, to: number, refocus: boolean) => {
    if (save.isPending) return;
    setDraftOrderIds((current) => {
      const currentIds = current ?? orderIds;
      return moveItem(currentIds, currentIds.indexOf(lessonId), to);
    });
    if (refocus) requestAnimationFrame(() => buttonRefs.current.get(lessonId)?.focus());
  };

  return (
    <>
      <section aria-label="Curriculum tools" className="flex shrink-0 flex-col">
        <div
          role="toolbar"
          aria-label="Curriculum actions"
          className="flex min-h-12 shrink-0 flex-wrap items-center gap-2 border-b bg-white px-2 py-2 sm:px-5"
        >
          <span className="font-display text-lg font-semibold">Curriculum</span>
          <StatusChip>{workingLessons.length} entries</StatusChip>
          <StatusChip tone="draft">
            {draftCount} draft{draftCount === 1 ? '' : 's'}
          </StatusChip>
          <Separator orientation="vertical" className="mx-1 h-5" />
          <Button
            size="sm"
            variant="outline"
            disabled={unsaved || save.isPending}
            onClick={() => newLessonJobs.add(selectedId)}
          >
            New lesson
          </Button>
          {newLessonJobs.sessions.map((session) => (
            <NewLessonSession
              key={session.id}
              state={newLessonJobs.getState(session)}
              lessons={data.lessons}
              selectedId={session.selectedId}
              onComplete={setSelectedId}
            />
          ))}
          <Button size="sm" variant="outline" disabled={save.isPending} onClick={openAdd}>
            Add entry
          </Button>
          {selectedIds.length ? <StatusChip tone="draft">{selectedIds.length} selected</StatusChip> : null}
          <Button size="sm" variant="outline" disabled={selectedIds.length < 2 || save.isPending} onClick={openMerge}>
            Merge…
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={selectedIds.length !== 1 || save.isPending}
            onClick={() => {
              const lesson = workingLessons.find((item) => item.lessonId === selectedIds[0]);
              if (lesson) openSplit(lesson);
            }}
          >
            Split…
          </Button>
          <Button size="sm" variant="outline" disabled={!selectedIds.length || save.isPending} onClick={openRemove}>
            Remove…
          </Button>
          <span className="hidden text-xs text-muted-foreground min-[1440px]:inline">
            Drag or press Alt+↑ / Alt+↓ to reorder.
          </span>
          <span className="flex-1" />
          <StatusChip tone={unsaved ? 'warning' : 'ok'}>{unsaved ? 'Unsaved edits' : 'Order saved'}</StatusChip>
          <Button size="sm" disabled={!unsaved || save.isPending} onClick={beginReview}>
            {save.isPending ? 'Saving…' : 'Save curriculum'}
          </Button>
        </div>
        {save.isError ? (
          <p role="alert" className="border-b px-5 py-2 text-sm text-destructive-90">
            {save.error.message}
          </p>
        ) : null}
        {data.findings?.length ? (
          <ul
            aria-label="Curriculum check findings"
            className="max-h-24 overflow-y-auto border-b px-5 py-2 text-xs text-destructive-90"
          >
            {data.findings.map((finding, index) => (
              <li key={index}>{finding.explanation}</li>
            ))}
          </ul>
        ) : null}
      </section>
      <div className="grid flex-none grid-cols-1 grid-rows-[--spacing(112)_auto_--spacing(96)] md:min-h-0 md:flex-1 md:grid-cols-[minmax(0,1fr)_clamp(20rem,24vw,24rem)_clamp(20rem,24vw,26rem)] md:grid-rows-[minmax(0,1fr)]">
        <main
          id="workbench-main"
          tabIndex={-1}
          aria-labelledby="entries-title"
          className="flex min-h-0 min-w-0 flex-col border-r bg-background"
        >
          <PaneHeader title="Lessons" titleId="entries-title" level={1}>
            <span className="ml-auto text-sm text-muted-foreground">Authored order</span>
          </PaneHeader>
          {workingLessons.length === 0 ? (
            <p role="status" className="p-6 text-center text-sm text-muted-foreground">
              No curriculum entries. Add slots to <code>content/curriculum/plan.yaml</code> and reload.
            </p>
          ) : (
            <ol className="m-0 min-h-0 flex-1 list-none overflow-y-auto px-2 pt-3 pb-16 md:px-4">
              {workingLessons.map((lesson, index) => {
                const entry = workingEntries[lesson.lessonId];
                const selected = lesson.lessonId === selectedId;
                return (
                  <li
                    key={lesson.lessonId}
                    draggable={!save.isPending}
                    data-entry-id={lesson.lessonId}
                    onDragStart={(event) => {
                      setDragging(lesson.lessonId);
                      event.dataTransfer.effectAllowed = 'move';
                    }}
                    onDragOver={(event) => {
                      if (dragging && dragging !== lesson.lessonId) event.preventDefault();
                    }}
                    onDrop={(event) => {
                      event.preventDefault();
                      if (dragging) move(dragging, index, false);
                    }}
                    onDragEnd={() => setDragging(null)}
                    className={cn(
                      'relative mb-1.5 grid grid-cols-[1.25rem_1.25rem_2.25rem_minmax(0,1fr)_auto_auto] items-center gap-2 rounded-xl border bg-white py-1.5 pr-2.5 pl-1.5 shadow-sm',
                      selected && 'border-primary-50 ring-2 ring-primary-10',
                      dragging === lesson.lessonId && 'opacity-45',
                    )}
                  >
                    <input
                      aria-label={`Select ${lesson.title}`}
                      type="checkbox"
                      className="size-4 accent-primary"
                      checked={selectedIds.includes(lesson.lessonId)}
                      onChange={(event) =>
                        setSelectedIds((current) =>
                          event.target.checked
                            ? [...current, lesson.lessonId]
                            : current.filter((id) => id !== lesson.lessonId),
                        )
                      }
                    />
                    <GripVerticalIcon aria-hidden className="size-4 cursor-grab text-secondary-50" />
                    <span
                      className={cn(
                        'rounded-md py-1 text-center text-xs font-bold',
                        moved.includes(lesson.lessonId) ? 'bg-primary text-white' : 'bg-secondary-10 text-secondary-90',
                      )}
                    >
                      {String(index + 1).padStart(2, '0')}
                    </span>
                    <button
                      type="button"
                      aria-label={`Open ${lesson.title} details`}
                      ref={(element) => {
                        if (element) buttonRefs.current.set(lesson.lessonId, element);
                        else buttonRefs.current.delete(lesson.lessonId);
                      }}
                      aria-pressed={selected}
                      onClick={() => setSelectedId(lesson.lessonId)}
                      onKeyDown={(event) => {
                        if (!event.altKey || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return;
                        event.preventDefault();
                        const to = index + (event.key === 'ArrowUp' ? -1 : 1);
                        if (to >= 0 && to < workingLessons.length) move(lesson.lessonId, to, true);
                      }}
                      className="col-span-full row-start-2 flex min-w-0 flex-col gap-1 rounded-md p-1 text-left focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none md:col-span-1 md:col-start-4 md:row-start-1"
                    >
                      <span className="leading-snug font-semibold">{lesson.title}</span>
                      <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                        <span>{lesson.cefrLevel}</span>
                        <StatusChip tone={lesson.source === 'draft' ? 'draft' : 'neutral'}>
                          {lesson.source === 'missing'
                            ? 'Not drafted'
                            : lesson.source === 'draft'
                              ? 'Draft'
                              : 'Canonical'}
                        </StatusChip>
                        {checkCounts?.[lesson.lessonId] !== undefined ? (
                          <StatusChip tone={checkCounts[lesson.lessonId] ? 'warning' : 'neutral'}>
                            {formatCheckCount(checkCounts[lesson.lessonId])}
                          </StatusChip>
                        ) : null}
                        {entry?.statusChips
                          .filter((chip) => !['canonical', 'draft', 'not drafted'].includes(chip.label.toLowerCase()))
                          .map((chip) => (
                            <StatusChip key={chip.label} tone={chip.tone}>
                              {chip.label}
                            </StatusChip>
                          ))}
                      </span>
                    </button>
                    {lesson.source !== 'missing' ? (
                      <Link
                        href={lessonHref(lesson.lessonId)}
                        className="col-start-5 row-start-1 rounded-md px-2.5 py-1 font-semibold text-primary hover:bg-primary-10"
                      >
                        Open<span className="sr-only"> {lesson.title}</span>
                      </Link>
                    ) : null}
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          type="button"
                          size="icon"
                          variant="ghost"
                          aria-label={`More actions for ${lesson.title}`}
                          className="col-start-6 row-start-1"
                        >
                          <MoreHorizontalIcon aria-hidden className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem onSelect={() => openSplit(lesson)}>Split entry</DropdownMenuItem>
                        <DropdownMenuItem
                          variant="destructive"
                          onSelect={() => {
                            setSelectedIds([lesson.lessonId]);
                            openEdit({
                              kind: 'remove',
                              lessonIds: [lesson.lessonId],
                              deleteNeverPromotedDrafts: false,
                            });
                          }}
                        >
                          Remove entry
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  </li>
                );
              })}
            </ol>
          )}
        </main>
        <section aria-labelledby="entry-title" className="flex min-h-0 flex-col border-r bg-white">
          <PaneHeader title="Entry" />
          <EntryDetail
            key={selectedId}
            lesson={workingLessons.find((lesson) => lesson.lessonId === selectedId)}
            data={{ ...data, entries: workingEntries }}
            onPreview={openEdit}
          />
        </section>
        <ChatPane
          contextOptions={[]}
          onOpenCurriculumEdit={openEdit}
          orderPending={save.isPending}
          turns={pendingTurn ? [...data.chat, pendingTurn] : data.chat}
          chips={chips}
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
          onAddChip={(chip) => setChips((current) => [...current, chip])}
          onRemoveChip={(index) => setChips((current) => current.filter((_, chipIndex) => chipIndex !== index))}
          onSend={(text) => {
            const message = text || 'Explain the curriculum entries.';
            setPendingTurn({ role: 'author', text: message, chips, toolNote: null });
            setChips([]);
            chat.mutate(
              { route: '/api/curriculum/chat', body: { message } },
              { onSuccess: () => setPendingTurn(null), onError: () => setPendingTurn(null) },
            );
          }}
        />
      </div>
      {dialogOpen && dialogEdit ? (
        <React.Suspense
          fallback={
            <p role="status" className="sr-only">
              Loading curriculum preview…
            </p>
          }
        >
          <CurriculumEditDialog
            open={dialogOpen}
            edit={dialogEdit}
            stagedEdits={pendingEdits}
            lessons={workingLessons}
            onEditChange={(edit) => setDialogEdit(edit)}
            onOpenChange={setDialogOpen}
            onAccept={acceptEdit}
          />
        </React.Suspense>
      ) : null}
      {reviewOpen ? (
        <React.Suspense
          fallback={
            <p role="status" className="sr-only">
              Loading curriculum review…
            </p>
          }
        >
          <CurriculumReviewDialog
            open={reviewOpen}
            edits={reviewEdits}
            saving={save.isPending}
            saveError={save.error?.message}
            onOpenChange={setReviewOpen}
            onSave={savePreviewedEdits}
          />
        </React.Suspense>
      ) : null}
    </>
  );
}

function formatCheckCount(count: number): string {
  return `${count} ${count === 1 ? 'check' : 'checks'}`;
}

export function CurriculumPage() {
  const curriculum = useCurriculum();
  if (!curriculum.data && curriculum.isPending) return <PageStatus>Loading curriculum…</PageStatus>;
  if (!curriculum.data)
    return (
      <PageStatus tone="error">
        Workbench unavailable: the curriculum plan could not be read. Fix content/curriculum/plan.yaml and reload.
      </PageStatus>
    );
  return (
    <>
      {curriculum.isError ? (
        <p role="alert" className="border-b border-warning-30 bg-warning-10 px-5 py-2 text-sm text-warning-90">
          Curriculum refresh failed: {curriculum.error.message} The last loaded plan and staged edits are still here.
        </p>
      ) : null}
      <CurriculumWorkspace data={curriculum.data} />
    </>
  );
}
