/** Entry point: `SourceEditor` owns one lesson's debounced source write and retry state. */

import * as React from 'react';
import { toast } from 'sonner';
import type { LessonPayload } from '@/api/contracts';
import { Button } from '@/components/ui/button';
import { Textarea } from '@/components/ui/textarea';
import { useWriteFile } from '@/lesson/api';

type PendingSourceEdit = { text: string; failed: boolean };

const K_WORKBENCH_PENDING_SOURCE_EDITS = new Map<string, PendingSourceEdit>();

export function SourceEditor({
  lesson,
  onBusyChange,
}: {
  lesson: LessonPayload;
  onBusyChange: (busy: boolean) => void;
}) {
  const { mutateAsync, isPending } = useWriteFile(lesson.lessonId);
  const pendingEdit = K_WORKBENCH_PENDING_SOURCE_EDITS.get(lesson.lessonId);
  const [text, setText] = React.useState(pendingEdit?.text ?? lesson.lessonSource);
  const [saved, setSaved] = React.useState(lesson.lessonSource);
  const [failed, setFailed] = React.useState<string | null>(pendingEdit?.failed ? pendingEdit.text : null);
  const mounted = React.useRef(false);
  const inFlight = React.useRef<string | null>(null);
  const pending = text !== saved || isPending;
  React.useEffect(() => onBusyChange(pending), [pending, onBusyChange]);
  React.useEffect(() => {
    if (text === saved && !isPending && lesson.lessonSource !== saved) {
      setText(lesson.lessonSource);
      setSaved(lesson.lessonSource);
    }
  }, [lesson.lessonSource, isPending, saved, text]);
  const save = React.useCallback(
    (captured: string) => {
      if (inFlight.current === captured) return;
      inFlight.current = captured;
      K_WORKBENCH_PENDING_SOURCE_EDITS.set(lesson.lessonId, { text: captured, failed: false });
      if (mounted.current) setFailed((value) => (value === captured ? null : value));
      void mutateAsync(captured)
        .then((result) => {
          if (K_WORKBENCH_PENDING_SOURCE_EDITS.get(lesson.lessonId)?.text === captured) {
            K_WORKBENCH_PENDING_SOURCE_EDITS.delete(lesson.lessonId);
            if (mounted.current) setFailed((value) => (value === captured ? null : value));
          }
          if (!mounted.current) return;
          setSaved(result.lesson.lessonSource);
          setText((current) => (current === captured ? result.lesson.lessonSource : current));
        })
        .catch((error: unknown) => {
          const edit = K_WORKBENCH_PENDING_SOURCE_EDITS.get(lesson.lessonId);
          if (edit?.text !== captured) return;
          K_WORKBENCH_PENDING_SOURCE_EDITS.set(lesson.lessonId, { ...edit, failed: true });
          if (mounted.current) setFailed(captured);
          toast.error(error instanceof Error ? error.message : 'Draft write failed');
        })
        .finally(() => {
          if (inFlight.current === captured) inFlight.current = null;
        });
    },
    [lesson.lessonId, mutateAsync],
  );
  const latest = React.useRef({ text, saved, isPending, save });
  latest.current = { text, saved, isPending, save };
  React.useEffect(() => {
    if (text === saved || isPending || text === failed) return;
    const timer = window.setTimeout(() => save(text), 800);
    return () => window.clearTimeout(timer);
  }, [failed, isPending, save, saved, text]);
  React.useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      const current = { ...latest.current, inFlight: inFlight.current };
      if (current.text !== current.saved || (current.isPending && current.inFlight !== current.text)) {
        current.save(current.text);
      }
    };
  }, []);
  return (
    <>
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-muted-foreground">Lesson text</span>
        <span role="status" className="text-xs text-muted-foreground">
          {failed
            ? 'Draft write failed'
            : pending
              ? 'Saving…'
              : lesson.draft.exists
                ? 'Saved to draft'
                : 'Source loaded'}
        </span>
      </div>
      <Textarea
        aria-label="Lesson source"
        value={text}
        onChange={(event) => {
          const next = event.target.value;
          setText(next);
          if (next === saved && !isPending) K_WORKBENCH_PENDING_SOURCE_EDITS.delete(lesson.lessonId);
          else K_WORKBENCH_PENDING_SOURCE_EDITS.set(lesson.lessonId, { text: next, failed: false });
          setFailed(null);
        }}
        spellCheck={false}
        className="mt-2 h-[50vh] field-sizing-fixed resize-y font-mono text-xs"
      />
      {failed ? (
        <Button className="mt-2" size="sm" variant="outline" onClick={() => save(text)}>
          Retry draft write
        </Button>
      ) : null}
      <section aria-label="Source check findings" className="mt-4 grid gap-2">
        {[
          ...lesson.sourceFindings.map((finding) => `${finding.code}: ${finding.explanation ?? finding.evidence}`),
          ...lesson.state.findings
            .filter((finding) => finding.origin === 'check')
            .map((finding) => `${finding.code}: ${finding.explanation}`),
          ...lesson.errors,
        ].map((finding, index) => (
          <p key={index} className="rounded-lg border bg-warning-10 p-3 text-sm">
            {finding}
          </p>
        ))}
      </section>
    </>
  );
}
