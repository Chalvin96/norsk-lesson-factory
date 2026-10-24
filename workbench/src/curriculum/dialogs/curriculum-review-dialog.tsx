import * as React from 'react';
import type { CurriculumEditPreview } from '@/api/contracts';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { usePreviewCurriculumEdits } from '@/curriculum/api';
import type { CurriculumEdit, CurriculumFindingDecisionInput } from '@/curriculum/types';

interface CurriculumReviewDialogProps {
  open: boolean;
  edits: CurriculumEdit[];
  saving: boolean;
  saveError?: string;
  onOpenChange: (open: boolean) => void;
  onSave: (input: {
    reviewDigest: string | null;
    findingDecisions: CurriculumFindingDecisionInput[];
    force: boolean;
  }) => void;
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
  const [decisions, setDecisions] = React.useState<Record<string, 'accept' | 'revise'>>({});
  const [confirmed, setConfirmed] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const { mutateAsync } = usePreviewCurriculumEdits();
  const requestId = React.useRef(0);

  React.useEffect(() => {
    if (!open || edits.length === 0) return;
    const id = ++requestId.current;
    setPreview(null);
    setDecisions({});
    setConfirmed(false);
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

  const reviewed =
    Boolean(preview) &&
    confirmed &&
    preview?.dependencyFindings.every((finding) => decisions[finding.findingId] === 'accept');
  const findingDecisions =
    preview?.dependencyFindings
      .filter((finding) => decisions[finding.findingId])
      .map((finding) => ({
        findingId: finding.findingId,
        decision: decisions[finding.findingId] as 'accept' | 'revise',
      })) ?? [];

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (saving && !nextOpen) return;
        onOpenChange(nextOpen);
      }}
    >
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Review curriculum changes</DialogTitle>
          <DialogDescription>
            Review the complete resulting order and decide each dependency finding before saving.
          </DialogDescription>
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
          <div className="grid gap-4">
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
                    <label className="grid gap-1 text-sm font-medium">
                      Decision
                      <select
                        aria-label={`Decision for ${finding.lessonId} prerequisite ${finding.prerequisiteId}`}
                        className="h-9 rounded-md border border-input bg-background px-2"
                        disabled={saving}
                        value={decisions[finding.findingId] ?? ''}
                        onChange={(event) =>
                          setDecisions((current) => ({
                            ...current,
                            [finding.findingId]: event.target.value as 'accept' | 'revise',
                          }))
                        }
                      >
                        <option value="">Choose a decision</option>
                        <option value="accept" disabled={finding.code === 'curriculum.prerequisite_missing'}>
                          Keep this finding and accept the dependency risk
                        </option>
                        <option value="revise">Revise the curriculum before saving</option>
                      </select>
                    </label>
                  </div>
                ))
              ) : (
                <p className="m-0 text-sm text-muted-foreground">No dependency findings.</p>
              )}
            </section>
            <label className="flex items-start gap-2 rounded-md border p-3 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 accent-primary"
                disabled={saving}
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              <span>
                I reviewed the current curriculum content, affected lessons, moved content, and each finding above.
              </span>
            </label>
          </div>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2 border-t pt-3">
          <Button
            type="button"
            disabled={!reviewed || loading || saving}
            onClick={() => {
              if (preview && reviewed) onSave({ reviewDigest: preview.reviewDigest, findingDecisions, force: false });
            }}
          >
            {saving ? 'Saving…' : 'Save reviewed curriculum'}
          </Button>
          <Button
            type="button"
            variant="destructive"
            disabled={!preview || loading || saving}
            onClick={() => onSave({ reviewDigest: null, findingDecisions: [], force: true })}
          >
            Force save unreviewed
          </Button>
          <Button type="button" variant="dialogSecondary" disabled={saving} onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
        </div>
        <p className="m-0 text-xs text-muted-foreground">
          A force save leaves curriculum review pending and blocks release until reviewed.
        </p>
      </DialogContent>
    </Dialog>
  );
}
