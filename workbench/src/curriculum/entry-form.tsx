import * as React from 'react';

import type { CurriculumEntry, CurriculumLesson } from '@/api/contracts';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Textarea } from '@/components/ui/textarea';
import type { CurriculumEdit, EntryEdit } from '@/curriculum/types';

import { parseLessonIds } from './form-values';

export function EntryFields({
  value,
  onChange,
  prefix,
}: {
  value: EntryEdit;
  onChange: (value: EntryEdit) => void;
  prefix: string;
}) {
  return (
    <>
      <div className="grid gap-1.5">
        <Label htmlFor={`${prefix}-title`}>Title</Label>
        <Input
          id={`${prefix}-title`}
          required
          value={value.title}
          onChange={(event) => onChange({ ...value, title: event.target.value })}
        />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor={`${prefix}-level`}>CEFR level</Label>
        <Select value={value.cefr_level} onValueChange={(cefr_level) => onChange({ ...value, cefr_level })}>
          <SelectTrigger id={`${prefix}-level`} className="w-full">
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
        <Label htmlFor={`${prefix}-outcome`}>Learner outcome</Label>
        <Textarea
          id={`${prefix}-outcome`}
          required
          rows={3}
          value={value.learner_outcome}
          onChange={(event) => onChange({ ...value, learner_outcome: event.target.value })}
        />
      </div>
    </>
  );
}

export function EntryForm({
  lesson,
  entry,
  onPreview,
  onClose,
}: {
  lesson: CurriculumLesson;
  entry: CurriculumEntry;
  onPreview: (edit: CurriculumEdit) => void;
  onClose: () => void;
}) {
  const [value, setValue] = React.useState<EntryEdit>({
    title: lesson.title,
    learner_outcome: entry.learnerOutcome,
    cefr_level: lesson.cefrLevel,
  });
  const [prerequisites, setPrerequisites] = React.useState(entry.dependsOn.join(', '));
  const [helpfulPrerequisites, setHelpfulPrerequisites] = React.useState(entry.helpfulPrerequisites.join(', '));
  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        onPreview({
          kind: 'update',
          lessonId: lesson.lessonId,
          title: value.title,
          learnerOutcome: value.learner_outcome,
          cefrLevel: value.cefr_level as 'A1' | 'A2' | 'B1' | 'B2',
          prerequisites: parseLessonIds(prerequisites),
          helpfulPrerequisites: parseLessonIds(helpfulPrerequisites),
        });
        onClose();
      }}
    >
      <EntryFields prefix="entry-edit" value={value} onChange={setValue} />
      <div className="grid gap-1.5">
        <Label htmlFor="entry-prerequisites">Required prerequisites</Label>
        <Input
          id="entry-prerequisites"
          value={prerequisites}
          onChange={(event) => setPrerequisites(event.target.value)}
          placeholder="Comma-separated lesson IDs"
        />
      </div>
      <div className="grid gap-1.5">
        <Label htmlFor="entry-helpful-prerequisites">Helpful prerequisites</Label>
        <Input
          id="entry-helpful-prerequisites"
          value={helpfulPrerequisites}
          onChange={(event) => setHelpfulPrerequisites(event.target.value)}
          placeholder="Comma-separated lesson IDs"
        />
      </div>
      <div className="flex gap-2">
        <Button size="sm" type="submit" disabled={!value.title.trim() || !value.learner_outcome.trim()}>
          Preview changes
        </Button>
        <Button size="sm" type="button" variant="outline" onClick={onClose}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
