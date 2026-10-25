import * as React from 'react';
import type { CurriculumEditPreview } from '@/api/contracts';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { usePreviewCurriculumEdits } from '@/curriculum/api';
import type { CurriculumEdit } from '@/curriculum/types';

interface CurriculumReviewDialogProps {
  open: boolean;
  edits: CurriculumEdit[];
  saving: boolean;
  saveError?: string;
  onOpenChange: (open: boolean) => void;
  onSave: (input: { reviewDigest: string | null }) => void;
}

export function CurriculumReviewDialog({
  open,
  edits,
  saving,
  saveError,
  onOpenChange,
  onSave,
}: CurriculumReviewDialogProps) {
  const [preview, setPreview] = React.useState<CurriculumEditPreview | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const { mutateAsync } = usePreviewCurriculumEdits();
  const requestId = React.useRef(0);

  React.useEffect(() => {
    if (!open || edits.length === 0) return;
    const id = ++requestId.current;
    setPreview(null);
    setError(null);
    setLoading(true);
    void mutateAsync(edits)
      .then((result) => {
        if (requestId.current === id) setPreview(result);
      })
      .catch((reason: unknown) => {
        if (requestId.current === id)
          setError(reason instanceof Error ? reason.message : 'Could not review the current curriculum.');
      })
      .finally(() => {
        if (requestId.current === id) setLoading(false);
      });
    return () => {
      requestId.current += 1;
    };
  }, [edits, mutateAsync, open]);

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (saving && !nextOpen) return;
        onOpenChange(nextOpen);
      }}
    >
      <DialogContent className="max-h-[90vh] max-w-2xl grid-cols-1 break-words overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Save curriculum changes</DialogTitle>
          <DialogDescription>Check the resulting order and affected lessons before saving.</DialogDescription>
        </DialogHeader>
        {loading ? (
          <p role="status" className="text-sm text-muted-foreground">
            Checking the current curriculum…
          </p>
        ) : null}
        {error ? (
          <p role="alert" className="text-sm text-destructive-90">
            {error}
          </p>
        ) : null}
        {saveError ? (
          <p role="alert" className="text-sm text-destructive-90">
            {saveError}
          </p>
        ) : null}
        {preview ? (
          <div className="grid min-w-0 grid-cols-1 gap-4">
            <section aria-label="Resulting authored order">
              <h3 className="font-semibold">Resulting authored order · {preview.entries.length} entries</h3>
              <ol className="mt-2 max-h-44 list-inside list-decimal overflow-y-auto text-sm">
                {preview.entries.map((entry) => (
                  <li key={entry.catalogId} value={entry.sequenceIndex + 1}>
                    {entry.title} <span className="text-muted-foreground">({entry.catalogId})</span>
                  </li>
                ))}
              </ol>
            </section>
            {preview.affectedLessons.length ? (
              <section aria-label="Lessons affected">
                <h3 className="font-semibold">Lessons affected</h3>
                <ul className="mt-1 grid gap-1 text-sm">
                  {preview.affectedLessons.map((item) => (
                    <li key={`${item.lessonId}-${item.effect}`}>
                      <strong>{item.title}</strong> — {item.detail}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            {preview.movedOutcomes.length || preview.movedPrerequisites.length ? (
              <section aria-label="Moved curriculum content">
                <h3 className="font-semibold">Content moved</h3>
                <ul className="mt-1 grid gap-1 text-sm">
                  {preview.movedOutcomes.map((item, index) => (
                    <li key={`outcome-${index}`}>
                      Outcome from {item.fromLessonId} moves to {item.toLessonId}: {item.outcome}
                    </li>
                  ))}
                  {preview.movedPrerequisites.map((item, index) => (
                    <li key={`prerequisite-${index}`}>
                      {item.lessonId}: {item.kind} prerequisite {item.fromLessonId} moves to {item.toLessonId}
                    </li>
                  ))}
                </ul>
              </section>
            ) : null}
            <section aria-label="Curriculum findings" className="grid gap-2">
              <h3 className="font-semibold">Dependency findings</h3>
              {preview.dependencyFindings.length ? (
                preview.dependencyFindings.map((finding) => (
                  <div
                    key={finding.findingId}
                    className="grid gap-2 rounded-lg border border-orange-40 bg-orange-10 p-3"
                  >
                    <p className="m-0 text-sm">{finding.explanation}</p>
                  </div>
                ))
              ) : (
                <p className="m-0 text-sm text-muted-foreground">No dependency findings.</p>
              )}
            </section>
          </div>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2 border-t pt-3">
          <Button
            type="button"
            disabled={
              !preview ||
              loading ||
              saving ||
              preview.dependencyFindings.some((finding) => finding.code === 'curriculum.prerequisite_missing')
            }
            onClick={() => {
              if (preview) onSave({ reviewDigest: preview.reviewDigest });
            }}
          >
            {saving ? 'Saving…' : 'Save curriculum'}
          </Button>

          <Button type="button" variant="dialogSecondary" disabled={saving} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
