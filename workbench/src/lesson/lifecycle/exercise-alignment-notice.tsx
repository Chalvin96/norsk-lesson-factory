import { keepReasons } from '@/lesson/review/decision-reasons';
/** Entry point: `ExerciseAlignmentNotice` shows an exercise's section alignment and owner history. */

import type { ExerciseAlignmentState } from '@/api/contracts';
import type { KeepDecision } from '@/lesson/review/decision-reasons';
import { KeepExerciseDialog } from './keep-exercise-dialog';

export interface ExerciseAlignmentNoticeProps {
  alignment?: ExerciseAlignmentState;
  exerciseKey: string;
  exerciseLabel: string;
  exercisePrompt?: React.ReactNode;
  disabled?: boolean;
  onKeep: (exerciseKey: string, decision: KeepDecision) => void;
}

export function ExerciseAlignmentNotice({
  alignment,
  exerciseKey,
  exerciseLabel,
  exercisePrompt,
  disabled,
  onKeep,
}: ExerciseAlignmentNoticeProps) {
  if (alignment?.status === 'stale') {
    return (
      <div role="status" className="mt-3 rounded-lg border border-warning-30 bg-warning-10 p-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded-full border border-warning-40 bg-white px-2 py-0.5 text-xs font-semibold text-warning-90">
            Stale
          </span>
          <span className="text-secondary-80">This exercise references changed lesson text.</span>
        </div>
        <ul lang="en" className="mt-2 list-disc pl-5">
          {alignment.changedSections.map((section) => (
            <li key={section.sectionId}>{section.summary}</li>
          ))}
        </ul>
        <div className="mt-2">
          <KeepExerciseDialog
            exerciseLabel={exerciseLabel}
            exercisePrompt={exercisePrompt}
            changedSections={alignment.changedSections.map((section) => {
              const heading = section.afterText?.split('\n')[0] ?? section.beforeText?.split('\n')[0];
              return heading?.replace(/^##\s+/, '').replace(/\s*\{.*\}\s*$/, '') || section.sectionId;
            })}
            disabled={disabled}
            onKeep={(note) => onKeep(exerciseKey, note)}
          />
        </div>
      </div>
    );
  }
  if (alignment?.status === 'untracked') {
    return (
      <p
        role="status"
        className="mt-3 inline-flex rounded-full border border-secondary-30 bg-secondary-10 px-2.5 py-1 text-xs font-semibold text-secondary-90"
      >
        Alignment not tracked
      </p>
    );
  }
  if (alignment?.status === 'aligned') {
    const history = [...alignment.history].sort((left, right) => right.at.localeCompare(left.at));
    return history.length ? (
      <section
        lang="en"
        aria-label="Exercise alignment history"
        className="mt-3 rounded-lg bg-secondary-0 px-3 py-2 text-xs text-secondary-80"
      >
        <h3 className="m-0 font-semibold">Decision history</h3>
        <ol className="m-0 mt-1 list-none space-y-1 p-0">
          {history.map((record) => (
            <li key={record.decisionId ?? `${record.at}-${record.outcome}`}>
              {record.at.slice(0, 10)} ·{' '}
              {record.outcome === 'kept' ? 'Kept' : record.outcome === 'approved' ? 'Approved' : 'Regenerated'}
              {record.outcome === 'kept'
                ? ` · ${keepReasons.find((reason) => reason.value === record.reason)?.label ?? 'Legacy decision'}`
                : ''}
              {record.comment || record.note ? ` · ${record.comment || record.note}` : ''}
            </li>
          ))}
        </ol>
      </section>
    ) : null;
  }
  return null;
}
