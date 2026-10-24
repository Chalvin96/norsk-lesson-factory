import type * as React from 'react';

type Span = { kind?: string; value?: unknown; lang?: string; children?: unknown };

export function Spans({ values }: { values: unknown }): React.ReactNode {
  if (typeof values === 'string') return values;
  if (!Array.isArray(values)) return values == null ? null : String(values);
  return values.map((span: Span, index) => {
    if (!span || typeof span !== 'object') return null;
    if (span.kind === 'sentence') return <Spans key={index} values={span.children} />;
    const value = String(span.value ?? '');
    switch (span.kind) {
      case 'emphasis':
        return <em key={index}>{value}</em>;
      case 'strong':
        return <strong key={index}>{value}</strong>;
      case 'code':
        return (
          <code key={index} className="rounded bg-secondary-10 px-1 text-[0.85em]">
            {value}
          </code>
        );
      case 'foreign_term':
        return (
          <span key={index} lang={span.lang ?? 'en'} className="font-medium text-primary-80">
            {value}
          </span>
        );
      default:
        return value;
    }
  });
}
