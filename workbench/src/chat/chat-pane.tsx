import * as React from 'react';

import type { ChatTurnData, ContextChipData } from '@/chat/types';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { ContextChip, PaneHeader, StatusChip } from '@/components/workbench';
import type { CurriculumEdit } from '@/curriculum/types';
import { JobErrorDisclosure } from '@/jobs/job-error-disclosure';

export interface ChatPaneProps {
  contextOptions?: readonly string[];
  turns: ChatTurnData[];
  chips: ContextChipData[];
  onSend: (text: string) => void;
  onAddChip: (chip: ContextChipData) => void;
  onRemoveChip: (index: number) => void;
  pendingSince?: number | null;
  partialText?: string;
  error?: string | null;
  errorDetail?: string | null;
  onRetry?: () => void;
  onRetryTurn?: (turnId: string) => void;
  onCancel?: () => void;
  onNewChat?: () => void;
  onOpenProposal?: (proposalId: string) => void;
  proposalIds?: string[];
  onStepProposal?: (direction: 1 | -1) => void;
  onOpenCurriculumEdit?: (edit: CurriculumEdit) => void;
  orderPending?: boolean;
  inputRef?: React.Ref<HTMLTextAreaElement>;
}

export function ChatPane({
  contextOptions = [],
  turns,
  chips,
  onSend,
  onAddChip,
  onRemoveChip,
  inputRef,
  pendingSince,
  partialText,
  error,
  errorDetail,
  onRetry,
  onRetryTurn,
  onCancel,
  onNewChat,
  onOpenProposal,
  proposalIds = [],
  onStepProposal,
  onOpenCurriculumEdit,
  orderPending,
}: ChatPaneProps) {
  const [elapsed, setElapsed] = React.useState(0);
  const [draft, setDraft] = React.useState('');
  const logRef = React.useRef<HTMLOListElement>(null);

  React.useEffect(() => {
    if (!pendingSince) return;
    const update = () => setElapsed(Math.floor((Date.now() - pendingSince) / 1000));
    update();
    const timer = window.setInterval(update, 1000);
    return () => window.clearInterval(timer);
  }, [pendingSince]);

  // biome-ignore lint/correctness/useExhaustiveDependencies: Scroll when displayed transcript content changes.
  React.useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight });
  }, [turns.length, pendingSince, partialText, error]);

  React.useEffect(() => {
    const focusChat = (event: KeyboardEvent) => {
      if (event.ctrlKey && event.key === '/') {
        event.preventDefault();
        document.getElementById('chat-message')?.focus();
      }
    };
    window.addEventListener('keydown', focusChat);
    return () => window.removeEventListener('keydown', focusChat);
  }, []);

  const submit = () => {
    if (pendingSince) return;
    if (!draft.trim() && chips.length === 0) return;
    onSend(draft.trim());
    setDraft('');
  };
  const hasTurnError = turns.some((turn) => turn.role === 'author' && turn.status === 'failed');
  const latestFailedTurnIndex = turns.reduce(
    (latest, turn, index) => (turn.role === 'author' && turn.status === 'failed' ? index : latest),
    -1,
  );

  return (
    <aside aria-labelledby="chat-title" className="flex min-h-0 min-w-0 flex-col bg-white">
      <PaneHeader title="Chat" titleId="chat-title">
        <StatusChip>Codex</StatusChip>
        <Button type="button" size="sm" variant="outline" onClick={onNewChat} disabled={Boolean(pendingSince)}>
          New chat
        </Button>
      </PaneHeader>
      {proposalIds.length ? (
        <div className="flex shrink-0 items-center gap-2 border-b px-3 py-1.5">
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => onStepProposal?.(-1)}
            aria-label="Previous pending proposal"
          >
            Previous proposal
          </Button>
          <span className="flex-1 text-center text-xs text-muted-foreground">
            {proposalIds.length} pending proposals
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => onStepProposal?.(1)}
            aria-label="Next pending proposal"
          >
            Next proposal
          </Button>
        </div>
      ) : null}
      <ol
        ref={logRef}
        aria-label="Chat transcript"
        tabIndex={0}
        aria-live="polite"
        className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring"
      >
        {turns.map((turn, index) => {
          const turnId = turn.turnId;
          const proposalId = turn.proposalId;
          const isLatestFailedTurn = index === latestFailedTurnIndex;
          return (
            <li
              key={turn.turnId ?? `${turn.role}-${index}`}
              className={`max-w-[92%] min-w-0 rounded-xl px-3 py-2.5 text-sm break-words ${
                turn.role === 'author' ? 'self-end rounded-br-sm bg-secondary-10' : 'self-start rounded-bl-sm border'
              }`}
            >
              <span className="text-[0.6rem] font-bold tracking-[0.16em] text-muted-foreground uppercase">
                {turn.role === 'author' ? 'You' : 'Codex'}
              </span>
              {turn.chips.length ? (
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {turn.chips.map((chip, chipIndex) => (
                    <ContextChip key={chipIndex} kind={chip.kind} label={chip.label} detail={chip.detail} />
                  ))}
                </div>
              ) : null}
              {turn.text ? <p className="mt-1 whitespace-pre-wrap">{turn.text}</p> : null}
              {turn.status === 'failed' ? (
                <div role="alert" className="mt-2 rounded-md border border-destructive-30 p-2 text-destructive-90">
                  <p>{(isLatestFailedTurn && error) || turn.error || 'This turn was interrupted.'}</p>
                  <JobErrorDisclosure detail={isLatestFailedTurn ? (errorDetail ?? null) : null} />
                  {turnId ? (
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      className="mt-2"
                      onClick={() => onRetryTurn?.(turnId)}
                    >
                      Retry
                    </Button>
                  ) : null}
                </div>
              ) : null}
              {turn.status === 'cancelled' && turnId ? (
                <div className="mt-2 flex items-center gap-2 text-xs text-muted-foreground">
                  <span>Cancelled</span>
                  <Button type="button" size="sm" variant="outline" onClick={() => onRetryTurn?.(turnId)}>
                    Retry
                  </Button>
                </div>
              ) : null}
              {turn.proposedEdit && onOpenCurriculumEdit ? (
                <div className="mt-3 grid gap-2 rounded-md border border-primary-30 p-2">
                  <p className="text-sm">Curriculum {turn.proposedEdit.kind} proposal</p>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={orderPending}
                    onClick={() => {
                      if (turn.proposedEdit) onOpenCurriculumEdit(turn.proposedEdit);
                    }}
                  >
                    Open in {turn.proposedEdit.kind} preview
                  </Button>
                  <p className="text-xs text-muted-foreground">
                    Nothing changes until you accept the preview and save the curriculum.
                  </p>
                </div>
              ) : null}
              {turn.toolNote ? (
                proposalId && onOpenProposal ? (
                  <button
                    type="button"
                    className="mt-2 block text-left text-xs font-semibold text-primary-90 underline underline-offset-2"
                    onClick={() => onOpenProposal(proposalId)}
                  >
                    {turn.toolNote}
                  </button>
                ) : (
                  <code className="mt-2 block rounded-md border border-primary-30 px-2 py-1.5 text-xs text-primary-90">
                    {turn.toolNote}
                  </code>
                )
              ) : null}
            </li>
          );
        })}
        {pendingSince ? (
          <li
            role="status"
            aria-label="Codex response in progress"
            className="rounded-xl border p-3 text-sm text-muted-foreground"
          >
            <span className="font-semibold text-foreground">Codex</span>
            {partialText ? (
              <p className="mt-1 whitespace-pre-wrap text-foreground">{partialText}</p>
            ) : (
              <p className="mt-1">Working…</p>
            )}
            <p className="mt-1 text-xs">{elapsed}s · provisional response</p>
          </li>
        ) : null}
        {error && !hasTurnError ? (
          <li role="alert" className="rounded-xl border border-destructive-30 p-3 text-sm text-destructive-90">
            <p>{error}</p>
            <JobErrorDisclosure detail={errorDetail ?? null} />
            {onRetry ? (
              <Button size="sm" variant="outline" className="mt-2" onClick={onRetry}>
                Retry
              </Button>
            ) : null}
          </li>
        ) : null}
      </ol>
      <form
        className="shrink-0 border-t p-3"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        {chips.length ? (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {chips.map((chip, index) => (
              <ContextChip
                key={index}
                kind={chip.kind}
                label={chip.label}
                detail={chip.detail}
                onRemove={() => onRemoveChip(index)}
              />
            ))}
          </div>
        ) : null}
        <Textarea
          id="chat-message"
          ref={inputRef}
          aria-label="Message to chat"
          rows={3}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder="Ask or instruct. In a lesson, select text and press Ctrl+L to reference it."
          className="max-h-48 min-h-18 resize-y"
        />
        <div className="mt-2 flex flex-wrap items-center gap-2">
          {contextOptions.length ? (
            <Select value="" onValueChange={(value) => onAddChip({ kind: 'file', label: value, detail: '' })}>
              <SelectTrigger size="sm" aria-label="Add context" className="w-40">
                <SelectValue placeholder="@ Add context" />
              </SelectTrigger>
              <SelectContent>
                {contextOptions.map((option) => (
                  <SelectItem key={option} value={option}>
                    {option}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null}
          <span className="text-xs text-muted-foreground">Edits arrive as proposals.</span>
          {pendingSince ? (
            <Button type="button" variant="outline" size="sm" className="ml-auto" onClick={onCancel}>
              Cancel
            </Button>
          ) : (
            <Button type="submit" disabled={!draft.trim() && chips.length === 0} size="sm" className="ml-auto">
              Send
            </Button>
          )}
        </div>
      </form>
    </aside>
  );
}
