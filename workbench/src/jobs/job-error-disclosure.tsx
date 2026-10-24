/** Entry point: `JobErrorDisclosure` keeps raw job failures available on every error surface. */

export function JobErrorDisclosure({
  detail,
  className = 'mt-2 text-xs',
}: {
  detail: string | null;
  className?: string;
}) {
  if (!detail) return null;

  return (
    <details className={className}>
      <summary className="w-fit cursor-pointer font-medium underline underline-offset-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none">
        Details
      </summary>
      <pre className="mt-1 whitespace-pre-wrap break-words">{detail}</pre>
    </details>
  );
}
