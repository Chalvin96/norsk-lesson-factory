import { XIcon } from 'lucide-react';
import type { ContextChipKind } from '@/api/contracts';
import { cn } from '@/lib/utils';

const KIND_STYLES: Record<ContextChipKind, string> = {
  selection: 'bg-primary-10 text-primary-90',
  finding: 'bg-warning-0 text-warning-90',
  file: 'bg-secondary-10 text-secondary-90',
};

export interface ContextChipProps {
  kind: ContextChipKind;
  label: string;
  detail?: string;
  onRemove?: () => void;
  className?: string;
}

export function ContextChip({ kind, label, detail, onRemove, className }: ContextChipProps) {
  return (
    <span
      data-kind={kind}
      className={cn(
        'relative inline-flex max-w-full min-w-0 flex-col rounded-md px-2 py-1 text-xs leading-snug',
        onRemove && 'pr-6',
        KIND_STYLES[kind],
        className,
      )}
    >
      <b className="text-[0.625rem] tracking-wider uppercase">{kind}</b>
      <span className="truncate">{label}</span>
      {detail ? (
        <span className="truncate" lang="nb">
          {detail}
        </span>
      ) : null}
      {onRemove ? (
        <button
          type="button"
          onClick={onRemove}
          aria-label={`Remove ${kind} ${label}`}
          className="absolute top-1 right-1 rounded-sm p-0.5 hover:bg-black/5 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <XIcon className="size-3" />
        </button>
      ) : null}
    </span>
  );
}
