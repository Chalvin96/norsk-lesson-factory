import type { WorkbenchFinding } from '@/api/contracts';
import { Button } from '@/components/ui/button';
import { findingReasons } from '@/lesson/review/decision-reasons';
import { cn } from '@/lib/utils';

export interface MarginNoteProps {
  id?: string;
  finding: WorkbenchFinding;
  isCurrent?: boolean;
  onAskChat?: (finding: WorkbenchFinding) => void;
  onResolve?: (finding: WorkbenchFinding) => void;
  similarFindingCount?: number;
  onResolveSimilar?: () => void;
  onFix?: (finding: WorkbenchFinding) => void;
  disabled?: boolean;
  className?: string;
}

export function MarginNote({
  id,
  finding,
  isCurrent = false,
  onAskChat,
  onResolve,
  similarFindingCount = 0,
  onResolveSimilar,
  onFix,
  disabled,
  className,
}: MarginNoteProps) {
  const isOpen = finding.status === 'open';
  const isError = finding.severity === 'error';
  return (
    <article
      id={id}
      tabIndex={-1}
      aria-label={`${finding.origin === 'ai' ? 'AI' : 'Check'} finding ${finding.label ?? 'Review finding'}`}
      data-status={finding.status}
      data-current={isCurrent || undefined}
      className={cn(
        'rounded-r-md border border-l-[3px] px-2.5 py-2 text-xs leading-relaxed focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
        isError
          ? 'border-destructive-20 border-l-destructive-60 bg-destructive-0'
          : 'border-warning-20 border-l-warning-50 bg-warning-0',
        !isOpen && 'border-dashed border-border border-l-secondary-40 bg-transparent',
        isCurrent && 'ring-2 ring-primary-50',
        className,
      )}
    >
      <header className="flex flex-wrap items-center gap-1.5">
        <span className="rounded-full bg-secondary-20 px-1.5 py-px text-[0.625rem] font-bold tracking-wider text-secondary-90 uppercase">
          {finding.origin === 'ai' ? 'AI' : 'Check'}
        </span>
        <strong>{finding.label ?? 'Review finding'}</strong>
        {finding.reviewer === 'second_opinion' ? (
          <span className="rounded-full bg-primary-10 px-1.5 py-px font-semibold text-primary-90">Second opinion</span>
        ) : null}
        {finding.advisory ? <span className="font-semibold">AI suggestion · unverified</span> : null}
      </header>
      <p className="mt-1 font-semibold">Severity · {finding.origin === 'ai' ? finding.level : finding.severity}</p>
      <p lang="en" className="mt-1 whitespace-pre-line">
        {finding.message || finding.explanation}
      </p>
      {finding.excerpt ? (
        <p lang="nb" className="mt-1 break-words">
          {finding.excerpt}
        </p>
      ) : null}
      {finding.quoteEnglish ? (
        <p lang="en" className="mt-1 text-muted-foreground">
          {finding.quoteEnglish}
        </p>
      ) : null}
      {finding.suggestedChange ? (
        <p lang="en" className="mt-1">
          <strong>Suggested change:</strong> {finding.suggestedChange}
        </p>
      ) : null}
      {finding.details ? (
        <details className="mt-1.5">
          <summary className="cursor-pointer font-semibold text-secondary-80">Details</summary>
          <pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap break-words text-[0.65rem]">
            {finding.details}
          </pre>
        </details>
      ) : null}
      {isOpen && similarFindingCount > 0 ? (
        <p className="mt-1 text-muted-foreground">{similarFindingCount} similar elsewhere</p>
      ) : null}
      {isOpen ? null : (
        <p className="mt-1 font-semibold text-accent-90">
          {finding.previouslyResolved ? 'Previously resolved' : 'Resolved'}
        </p>
      )}
      {!isOpen && finding.origin === 'ai' ? (
        <div className="mt-1">
          <p>
            {findingReasons.find((reason) => reason.value === finding.resolutionReason)?.label ?? 'Legacy decision'}
          </p>
          {finding.resolutionComment || finding.resolutionNote ? (
            <p>{finding.resolutionComment || finding.resolutionNote}</p>
          ) : null}
        </div>
      ) : null}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {onAskChat ? (
          <Button size="sm" variant="outline" className="h-7 px-2 text-xs" onClick={() => onAskChat(finding)}>
            Ask chat
          </Button>
        ) : null}
        {isOpen && finding.advisory && onFix ? (
          <Button
            size="sm"
            variant="outline"
            disabled={disabled}
            className="h-7 px-2 text-xs"
            onClick={() => onFix(finding)}
          >
            Fix
          </Button>
        ) : null}
        {isOpen && onResolve ? (
          <Button
            size="sm"
            variant="outline"
            disabled={disabled}
            className="h-7 px-2 text-xs"
            onClick={() => onResolve(finding)}
          >
            Resolve
          </Button>
        ) : null}
        {isOpen && similarFindingCount > 0 && onResolveSimilar ? (
          <Button
            size="sm"
            variant="outline"
            disabled={disabled}
            className="h-7 px-2 text-xs"
            onClick={onResolveSimilar}
          >
            Resolve all similar ({similarFindingCount})
          </Button>
        ) : null}
      </div>
    </article>
  );
}
