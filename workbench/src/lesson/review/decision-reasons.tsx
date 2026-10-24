/** Entry point: `DecisionReasons` renders keyboard-operable owner reason chips. */
import * as React from 'react';
import type { WorkbenchWire } from '@/api/contracts';

export const decisionCommentMaxChars = 500;

export type KeepDecision = Required<Pick<WorkbenchWire.KeepRequest, 'reason' | 'comment'>>;
export type FindingReason = NonNullable<WorkbenchWire.FindingResolveRequest['reason']>;
export const findingReasons = [
  { value: 'intended', label: 'Intended' },
  { value: 'not_applicable', label: 'Not applicable' },
  { value: 'fixed_elsewhere', label: 'Fixed elsewhere' },
  { value: 'reviewer_wrong', label: 'Reviewer is wrong' },
] satisfies { value: FindingReason; label: string }[];
export const keepReasons = [
  { value: 'still_matches', label: 'Still matches the section' },
  { value: 'unaffected', label: "Change doesn't affect it" },
  { value: 'revise_later', label: "I'll revise it later" },
] satisfies { value: NonNullable<KeepDecision['reason']>; label: string }[];

export function DecisionReasons<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  const name = React.useId();
  return (
    <div className="min-w-0" role="radiogroup" aria-labelledby={`${name}-label`}>
      <p id={`${name}-label`} className="mb-2 text-sm font-semibold">
        Reason
      </p>
      <div className="flex flex-wrap gap-2">
        {options.map((option) => (
          <label key={option.value} className="relative cursor-pointer">
            <input
              type="radio"
              name={name}
              value={option.value}
              checked={value === option.value}
              onChange={() => onChange(option.value)}
              className="peer sr-only"
            />
            <span className="inline-flex min-h-9 items-center gap-1.5 rounded-full border border-secondary-60 px-3 py-1.5 text-sm peer-checked:border-primary peer-checked:bg-primary-10 peer-checked:font-semibold peer-checked:text-primary-90 peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-primary">
              {value === option.value ? <span aria-hidden="true">✓</span> : null}
              {option.label}
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}
