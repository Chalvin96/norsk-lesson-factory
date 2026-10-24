import * as React from 'react';
import type { CurriculumEditPreview, CurriculumLesson } from '@/api/contracts';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import { usePreviewCurriculumEdits } from '@/curriculum/api';
import type { CurriculumEdit } from '@/curriculum/types';

import { deriveSlug, parseLessonIds } from '../form-values';

interface CurriculumEditDialogProps {
  open: boolean;
  edit: CurriculumEdit | null;
  stagedEdits: CurriculumEdit[];
  lessons: CurriculumLesson[];
  onEditChange: (edit: CurriculumEdit) => void;
  onOpenChange: (open: boolean) => void;
  onAccept: (edit: CurriculumEdit, preview: CurriculumEditPreview) => void;
}

export function CurriculumEditDialog({
  open,
  edit,
  stagedEdits,
  lessons,
  onEditChange,
  onOpenChange,
  onAccept,
}: CurriculumEditDialogProps) {
  const [editPreview, setEditPreview] = React.useState<CurriculumEditPreview | null>(null);
  const [cumulativePreview, setCumulativePreview] = React.useState<CurriculumEditPreview | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [loading, setLoading] = React.useState(false);
  const editIdentity = edit === null ? '' : edit.kind === 'update' ? `update:${edit.lessonId}` : edit.kind;
  const initialPrerequisites =
    edit?.kind === 'add'
      ? edit.entry.prerequisites.join(', ')
      : edit?.kind === 'update'
        ? edit.prerequisites.join(', ')
        : '';
  const initialHelpfulPrerequisites =
    edit?.kind === 'add'
      ? edit.entry.helpfulPrerequisites.join(', ')
      : edit?.kind === 'update'
        ? edit.helpfulPrerequisites.join(', ')
        : '';
  const [prerequisiteInputs, setPrerequisiteInputs] = React.useState({
    identity: editIdentity,
    required: initialPrerequisites,
    helpful: initialHelpfulPrerequisites,
  });
  const currentPrerequisiteInputs =
    prerequisiteInputs.identity === editIdentity
      ? prerequisiteInputs
      : { identity: editIdentity, required: initialPrerequisites, helpful: initialHelpfulPrerequisites };
  React.useEffect(() => {
    setPrerequisiteInputs({
      identity: editIdentity,
      required: initialPrerequisites,
      helpful: initialHelpfulPrerequisites,
    });
  }, [editIdentity, initialHelpfulPrerequisites, initialPrerequisites]);
  const { mutateAsync } = usePreviewCurriculumEdits();
  const requestId = React.useRef(0);
  const previewEdit = React.useMemo(() => {
    if (edit?.kind === 'add')
      return {
        ...edit,
        entry: {
          ...edit.entry,
          prerequisites: parseLessonIds(currentPrerequisiteInputs.required),
          helpfulPrerequisites: parseLessonIds(currentPrerequisiteInputs.helpful),
        },
      };
    if (edit?.kind === 'update')
      return {
        ...edit,
        prerequisites: parseLessonIds(currentPrerequisiteInputs.required),
        helpfulPrerequisites: parseLessonIds(currentPrerequisiteInputs.helpful),
      };
    return edit;
  }, [currentPrerequisiteInputs.helpful, currentPrerequisiteInputs.required, edit]);

  React.useEffect(() => {
    if (!open || !previewEdit) return;
    const id = ++requestId.current;
    setEditPreview(null);
    setCumulativePreview(null);
    setError(null);
    setLoading(true);
    const timer = window.setTimeout(() => {
      const allEdits = [...stagedEdits, previewEdit];
      const request = stagedEdits.length
        ? Promise.all([mutateAsync(stagedEdits), mutateAsync(allEdits)])
        : mutateAsync(allEdits).then((result) => [null, result] as const);
      void request
        .then(([prefix, cumulative]) => {
          if (requestId.current === id) {
            setEditPreview(getIncrementalPreview(prefix, cumulative));
            setCumulativePreview(cumulative);
          }
        })
        .catch((reason: unknown) => {
          if (requestId.current === id)
            setError(reason instanceof Error ? reason.message : 'Could not preview this curriculum edit.');
        })
        .finally(() => {
          if (requestId.current === id) setLoading(false);
        });
    }, 220);
    return () => {
      window.clearTimeout(timer);
      requestId.current += 1;
    };
  }, [mutateAsync, open, previewEdit, stagedEdits]);

  if (!edit) return null;
  const title =
    edit.kind === 'merge'
      ? 'Merge entries'
      : edit.kind === 'split'
        ? 'Split entry'
        : edit.kind === 'remove'
          ? 'Remove entries'
          : edit.kind === 'add'
            ? 'Add curriculum entry'
            : edit.kind === 'update'
              ? 'Edit curriculum entry'
              : 'Reorder curriculum';
  const selectedLessons =
    edit.kind === 'merge' || edit.kind === 'remove'
      ? edit.lessonIds
          .map((id) => lessons.find((lesson) => lesson.lessonId === id))
          .filter((lesson): lesson is CurriculumLesson => Boolean(lesson))
      : edit.kind === 'split' || edit.kind === 'update'
        ? lessons.filter((lesson) => lesson.lessonId === edit.lessonId)
        : [];
  const keptLesson =
    edit.kind === 'merge' ? lessons.find((lesson) => edit.lessonIds.includes(lesson.lessonId)) : undefined;
  const keptPosition = keptLesson ? lessons.findIndex((lesson) => lesson.lessonId === keptLesson.lessonId) + 1 : null;
  const canDeleteDrafts = selectedLessons.length > 0 && selectedLessons.every((lesson) => lesson.source === 'draft');

  const setMerge = (field: 'title' | 'learnerOutcome', value: string) => {
    if (edit.kind === 'merge') onEditChange({ ...edit, [field]: value });
  };
  const setUpdate = (field: 'title' | 'learnerOutcome', value: string) => {
    if (edit.kind === 'update') onEditChange({ ...edit, [field]: value });
  };
  const setAdd = (field: 'title' | 'learnerOutcome', value: string) => {
    if (edit.kind !== 'add') return;
    const entry = { ...edit.entry, [field]: value };
    if (
      field === 'title' &&
      (edit.entry.catalogId === 'new_curriculum_entry' || edit.entry.catalogId === deriveSlug(edit.entry.title))
    )
      entry.catalogId = deriveSlug(value) || 'new_curriculum_entry';
    onEditChange({ ...edit, entry });
  };
  const setSplitPart = (index: number, field: 'catalogId' | 'title' | 'learnerOutcome', value: string) => {
    if (edit.kind !== 'split') return;
    const [first, second, ...additional] = edit.parts;
    const updatedPart = (part: (typeof edit.parts)[number], partIndex: number) =>
      partIndex === index ? { ...part, [field]: value } : part;
    const parts: typeof edit.parts = [
      updatedPart(first, 0),
      updatedPart(second, 1),
      ...additional.map((part, partIndex) => updatedPart(part, partIndex + 2)),
    ];
    onEditChange({ ...edit, parts });
  };
  const canAccept = Boolean(
    cumulativePreview &&
      !loading &&
      !error &&
      (edit.kind !== 'merge' || Boolean(edit.title.trim() && edit.learnerOutcome.trim())),
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>
            Review this edit’s impact first. Accepted edits remain local until you save the curriculum.
          </DialogDescription>
        </DialogHeader>
        {edit.kind === 'merge' ? (
          <div className="grid gap-3">
            <p className="text-sm text-muted-foreground">
              Merging {selectedLessons.length} entries into one. Keeps ID{' '}
              <code>{keptLesson?.lessonId ?? edit.lessonIds[0]}</code>
              {keptPosition ? ` (position ${String(keptPosition).padStart(2, '0')})` : ''} in authored order.
            </p>
            <Field label="Title" value={edit.title} onChange={(value) => setMerge('title', value)} />
            <TextField
              label="Learner outcome"
              value={edit.learnerOutcome}
              onChange={(value) => setMerge('learnerOutcome', value)}
            />
          </div>
        ) : null}
        {edit.kind === 'split' ? (
          <div className="grid gap-3">
            <p className="text-sm text-muted-foreground">
              The first part keeps the current entry ID. Later parts get new IDs and follow it in authored order.
            </p>
            {edit.parts.map((part, index) => (
              <fieldset key={index} className="grid gap-3 rounded-lg border p-3">
                <legend className="px-1 text-xs font-semibold uppercase tracking-wide">Part {index + 1}</legend>
                <Field
                  label={`Part ${index + 1} lesson ID`}
                  value={part.catalogId}
                  disabled={index === 0}
                  onChange={(value) => setSplitPart(index, 'catalogId', value)}
                />
                <Field
                  label={`Part ${index + 1} title`}
                  value={part.title}
                  onChange={(value) => setSplitPart(index, 'title', value)}
                />
                <TextField
                  label={`Part ${index + 1} learner outcome`}
                  value={part.learnerOutcome}
                  onChange={(value) => setSplitPart(index, 'learnerOutcome', value)}
                />
                {index > 1 ? (
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      const [first, second, ...additional] = edit.parts;
                      onEditChange({
                        ...edit,
                        parts: [first, second, ...additional.filter((_, partIndex) => partIndex + 2 !== index)],
                      });
                    }}
                  >
                    Remove part {index + 1}
                  </Button>
                ) : null}
              </fieldset>
            ))}
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="w-fit"
              onClick={() =>
                onEditChange({
                  ...edit,
                  parts: [...edit.parts, { catalogId: `part_${edit.parts.length + 1}`, title: '', learnerOutcome: '' }],
                })
              }
            >
              Add part
            </Button>
          </div>
        ) : null}
        {edit.kind === 'remove' ? (
          <div className="grid gap-3">
            <p className="text-sm text-muted-foreground">
              These entries leave the curriculum. Lesson packages stay in the workspace unless you choose to delete
              eligible drafts.
            </p>
            <ul className="grid gap-1 text-sm">
              {selectedLessons.map((lesson) => (
                <li key={lesson.lessonId}>
                  {lesson.title} ·{' '}
                  {lesson.source === 'draft'
                    ? 'draft package'
                    : lesson.source === 'canonical'
                      ? 'canonical package'
                      : 'no package'}
                </li>
              ))}
            </ul>
            <label className="flex items-start gap-2 rounded-md border p-3 text-sm">
              <input
                type="checkbox"
                className="mt-0.5 accent-primary"
                checked={edit.deleteNeverPromotedDrafts}
                disabled={!canDeleteDrafts}
                onChange={(event) => onEditChange({ ...edit, deleteNeverPromotedDrafts: event.target.checked })}
              />
              <span>
                Delete draft packages that have never been promoted. This is available only when every selected entry is
                still a draft.
              </span>
            </label>
          </div>
        ) : null}
        {edit.kind === 'add' ? (
          <div className="grid gap-3">
            <p className="text-sm text-muted-foreground">Creates a plan entry without generating a lesson package.</p>
            <Field
              label="Lesson ID"
              value={edit.entry.catalogId}
              onChange={(value) => onEditChange({ ...edit, entry: { ...edit.entry, catalogId: value } })}
            />
            <Field label="Title" value={edit.entry.title} onChange={(value) => setAdd('title', value)} />
            <TextField
              label="Learner outcome"
              value={edit.entry.learnerOutcome}
              onChange={(value) => setAdd('learnerOutcome', value)}
            />
            <div className="grid gap-1.5">
              <Label htmlFor="curriculum-add-level">CEFR level</Label>
              <Select
                value={edit.entry.cefrLevel}
                onValueChange={(cefrLevel: 'A1' | 'A2' | 'B1' | 'B2') =>
                  onEditChange({ ...edit, entry: { ...edit.entry, cefrLevel } })
                }
              >
                <SelectTrigger id="curriculum-add-level">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {['A1', 'A2', 'B1', 'B2'].map((level) => (
                    <SelectItem key={level} value={level}>
                      {level}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="curriculum-add-position">Insert after</Label>
              <Select
                value={edit.afterLessonId ?? '__beginning__'}
                onValueChange={(value) =>
                  onEditChange({ ...edit, afterLessonId: value === '__beginning__' ? null : value })
                }
              >
                <SelectTrigger id="curriculum-add-position">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="__beginning__">Beginning</SelectItem>
                  {lessons.map((lesson) => (
                    <SelectItem key={lesson.lessonId} value={lesson.lessonId}>
                      After {lesson.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Field
              label="Required prerequisites (comma-separated IDs)"
              value={currentPrerequisiteInputs.required}
              onChange={(value) => setPrerequisiteInputs({ ...currentPrerequisiteInputs, required: value })}
            />
            <Field
              label="Helpful prerequisites (comma-separated IDs)"
              value={currentPrerequisiteInputs.helpful}
              onChange={(value) => setPrerequisiteInputs({ ...currentPrerequisiteInputs, helpful: value })}
            />
          </div>
        ) : null}
        {edit.kind === 'update' ? (
          <div className="grid gap-3">
            <Field label="Title" value={edit.title} onChange={(value) => setUpdate('title', value)} />
            <TextField
              label="Learner outcome"
              value={edit.learnerOutcome}
              onChange={(value) => setUpdate('learnerOutcome', value)}
            />
            <div className="grid gap-1.5">
              <Label htmlFor="curriculum-update-level">CEFR level</Label>
              <Select
                value={edit.cefrLevel}
                onValueChange={(cefrLevel: 'A1' | 'A2' | 'B1' | 'B2') => onEditChange({ ...edit, cefrLevel })}
              >
                <SelectTrigger id="curriculum-update-level">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {['A1', 'A2', 'B1', 'B2'].map((level) => (
                    <SelectItem key={level} value={level}>
                      {level}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Field
              label="Required prerequisites (comma-separated IDs)"
              value={currentPrerequisiteInputs.required}
              onChange={(value) => setPrerequisiteInputs({ ...currentPrerequisiteInputs, required: value })}
            />
            <Field
              label="Helpful prerequisites (comma-separated IDs)"
              value={currentPrerequisiteInputs.helpful}
              onChange={(value) => setPrerequisiteInputs({ ...currentPrerequisiteInputs, helpful: value })}
            />
          </div>
        ) : null}
        {edit.kind === 'reorder' ? (
          <p className="text-sm text-muted-foreground">Review the resulting authored order before accepting it.</p>
        ) : null}

        <section aria-label="This edit impact" className="grid gap-3 border-t pt-4">
          <h3 className="font-display text-lg font-semibold">{loading ? 'Updating preview…' : 'This edit'}</h3>
          {error ? (
            <p role="alert" className="text-sm text-destructive-90">
              {error}
            </p>
          ) : null}
          {editPreview ? <CurriculumPreviewImpact preview={editPreview} /> : null}
        </section>
        {stagedEdits.length ? (
          <details className="rounded-md border p-3">
            <summary className="cursor-pointer text-sm font-semibold">
              All staged edits ({stagedEdits.length + 1})
            </summary>
            {cumulativePreview ? <CurriculumPreviewImpact preview={cumulativePreview} /> : null}
          </details>
        ) : null}
        <div className="flex flex-wrap justify-end gap-2 border-t pt-3">
          <Button
            type="button"
            variant={edit.kind === 'remove' ? 'destructive' : 'default'}
            disabled={!canAccept}
            onClick={() => {
              if (previewEdit && cumulativePreview) onAccept(previewEdit, cumulativePreview);
            }}
          >
            {edit.kind === 'remove'
              ? 'Accept removal'
              : edit.kind === 'merge'
                ? 'Accept merge'
                : edit.kind === 'split'
                  ? 'Accept split'
                  : 'Accept edit'}
          </Button>
          <Button type="button" variant="dialogSecondary" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function getIncrementalPreview(
  prefix: CurriculumEditPreview | null,
  cumulative: CurriculumEditPreview,
): CurriculumEditPreview {
  if (!prefix) return cumulative;
  return {
    ...cumulative,
    affectedLessons: subtractPreviewItems(cumulative.affectedLessons, prefix.affectedLessons),
    movedOutcomes: subtractPreviewItems(cumulative.movedOutcomes, prefix.movedOutcomes),
    movedPrerequisites: subtractPreviewItems(cumulative.movedPrerequisites, prefix.movedPrerequisites),
    dependencyFindings: subtractPreviewItems(cumulative.dependencyFindings, prefix.dependencyFindings),
  };
}

function subtractPreviewItems<T>(cumulative: T[], prefix: T[]): T[] {
  const matched = new Set<number>();
  return cumulative.filter((item) => {
    const matchingIndex = prefix.findIndex(
      (candidate, index) => !matched.has(index) && JSON.stringify(candidate) === JSON.stringify(item),
    );
    if (matchingIndex < 0) return true;
    matched.add(matchingIndex);
    return false;
  });
}

function CurriculumPreviewImpact({ preview }: { preview: CurriculumEditPreview }) {
  return (
    <div className="grid gap-3">
      {preview.affectedLessons.length ? (
        <div>
          <h4 className="text-xs font-semibold tracking-wide uppercase">Lessons affected</h4>
          <ul className="mt-1 grid gap-1 text-sm">
            {preview.affectedLessons.map((item, index) => (
              <li key={`${item.lessonId}-${index}`}>
                <strong>{item.title}</strong> — {item.detail}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {preview.movedOutcomes.length ? (
        <div>
          <h4 className="text-xs font-semibold tracking-wide uppercase">Outcomes moved</h4>
          <ul className="mt-1 grid gap-1 text-sm">
            {preview.movedOutcomes.map((item, index) => (
              <li key={`${item.fromLessonId}-${index}`}>
                {item.outcome} → {item.toLessonId}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {preview.movedPrerequisites.length ? (
        <div>
          <h4 className="text-xs font-semibold tracking-wide uppercase">Prerequisites re-linked</h4>
          <ul className="mt-1 grid gap-1 text-sm">
            {preview.movedPrerequisites.map((item, index) => (
              <li key={`${item.lessonId}-${item.fromLessonId}-${index}`}>
                {item.lessonId}: {item.fromLessonId} → {item.toLessonId} ({item.kind})
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {preview.dependencyFindings.length ? (
        <div role="alert" className="rounded-md border border-orange-40 bg-orange-10 p-2">
          <h4 className="text-xs font-semibold tracking-wide uppercase">Dependency findings</h4>
          <ul className="mt-1 grid gap-1 text-sm">
            {preview.dependencyFindings.map((item, index) => (
              <li key={`${item.lessonId}-${index}`}>{item.explanation}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <details className="rounded-md border p-2">
        <summary className="cursor-pointer text-sm font-semibold">
          Resulting authored order · {preview.entries.length} entries
        </summary>
        <ol className="mt-2 max-h-52 list-inside list-decimal overflow-y-auto text-sm">
          {preview.entries.map((item) => (
            <li key={item.catalogId} value={item.sequenceIndex + 1}>
              {item.title} <span className="text-muted-foreground">({item.catalogId})</span>
            </li>
          ))}
        </ol>
      </details>
    </div>
  );
}

function Field({
  label,
  value,
  disabled = false,
  onChange,
}: {
  label: string;
  value: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  const id = React.useId();
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} value={value} disabled={disabled} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

function TextField({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  const id = React.useId();
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Textarea id={id} required rows={3} value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}
