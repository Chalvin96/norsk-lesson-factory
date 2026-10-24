/** Entry point: `KeepExerciseDialog` collects an owner reason and optional comment for a keep decision. */

import * as React from 'react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Textarea } from '@/components/ui/textarea';
import {
  DecisionReasons,
  decisionCommentMaxChars,
  type KeepDecision,
  keepReasons,
} from '@/lesson/review/decision-reasons';

export interface KeepExerciseDialogProps {
  exerciseLabel: string;
  exercisePrompt?: React.ReactNode;
  changedSections?: string[];
  disabled?: boolean;
  onKeep: (decision: KeepDecision) => void;
}

export function KeepExerciseDialog({
  exerciseLabel,
  exercisePrompt,
  changedSections,
  disabled,
  onKeep,
}: KeepExerciseDialogProps) {
  const [open, setOpen] = React.useState(false);
  const [note, setNote] = React.useState('');

  const [reason, setReason] = React.useState<NonNullable<KeepDecision['reason']>>('still_matches');
  const commentId = React.useId();
  const keep = () => {
    const trimmed = note.trim();
    onKeep({ reason, comment: trimmed });
    setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button type="button" size="sm" variant="outline" disabled={disabled}>
          Keep as is
        </Button>
      </DialogTrigger>
      <DialogContent className="max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] overflow-y-auto rounded-2xl sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>Keep exercise as is</DialogTitle>
          <DialogDescription>
            A section this exercise is built from has changed. The exercise stays unchanged and is no longer marked
            stale; your reason is kept with its approval.
          </DialogDescription>
        </DialogHeader>
        <div className="min-w-0 rounded-xl bg-secondary-10 p-3.5 text-sm break-words">
          <p className="text-xs text-secondary-80">{exerciseLabel}</p>
          {exercisePrompt ? <div className="mt-1 font-semibold">{exercisePrompt}</div> : null}
          {changedSections?.map((title) => (
            <p key={title} className="mt-1 text-secondary-80">
              Changed section: {title}
            </p>
          ))}
        </div>
        <DecisionReasons options={keepReasons} value={reason} onChange={setReason} />
        <label htmlFor={commentId} className="text-sm font-semibold">
          Comment (optional)
        </label>
        <Textarea
          id={commentId}
          maxLength={decisionCommentMaxChars}
          aria-describedby={`${commentId}-count`}
          value={note}
          onChange={(event) => setNote(event.currentTarget.value)}
          placeholder="Anything worth remembering about this decision"
        />
        <p id={`${commentId}-count`} className="text-xs text-secondary-80">
          {note.length} / {decisionCommentMaxChars}
        </p>
        <DialogFooter>
          <Button type="button" variant="dialogSecondary" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button type="button" disabled={disabled} onClick={keep}>
            Keep as is
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
