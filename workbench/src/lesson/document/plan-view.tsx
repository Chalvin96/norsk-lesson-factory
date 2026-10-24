import type { PlanSourceProjection } from '@/api/contracts';
import { Badge } from '@/components/ui/badge';

const K_PLAN_VIEW_AUTHOR_FIELDS: readonly (readonly [key: string, label: string])[] = [
  ['title', 'Title'],
  ['cefr_level', 'CEFR level'],
  ['learner_outcome', 'Learner outcome'],
  ['objectives', 'Objectives'],
  ['teaching_points', 'Teaching points'],
  ['coverage', 'Coverage'],
  ['success_criteria', 'Success criteria'],
  ['exclusions', 'Exclusions'],
  ['kind', 'Kind'],
  ['family_id', 'Family'],
  ['cefr_tags', 'CEFR tags'],
  ['terminology_ids', 'Terminology'],
  ['prerequisites', 'Prerequisites'],
  ['helpful_prerequisites', 'Helpful prerequisites'],
];

const K_PLAN_VIEW_TECHNICAL_KEYS = new Set([
  'package_version',
  'lesson_id',
  'catalog_id',
  'cefr_target_basis',
  'approved',
  'approved_source_hash',
  'source_owner',
  'source_owners',
]);
const K_PLAN_VIEW_AUTHOR_KEYS = new Set(K_PLAN_VIEW_AUTHOR_FIELDS.map(([key]) => key));

function hasMeaningfulValue(value: unknown): boolean {
  if (value === null || value === undefined) return false;
  if (Array.isArray(value)) return value.some(hasMeaningfulValue);
  if (typeof value === 'object') return Object.values(value).some(hasMeaningfulValue);
  return typeof value !== 'string' || value.trim().length > 0;
}

function MarkdownBody({ source }: { source: string }) {
  const lines = source.split('\n');
  const blocks: { kind: 'paragraph' | 'list'; lines: string[] }[] = [];
  for (const line of lines) {
    const listItem = /^\s*[-*+]\s+/.test(line);
    if (!line.trim()) continue;
    const kind = listItem ? 'list' : 'paragraph';
    const current = blocks.at(-1);
    if (current?.kind === kind) current.lines.push(line.replace(/^\s*[-*+]\s+/, ''));
    else blocks.push({ kind, lines: [line.replace(/^\s*[-*+]\s+/, '')] });
  }
  return blocks.map((block, index) => {
    if (block.kind === 'list')
      return (
        <ul key={index} className="my-3 list-disc space-y-1 pl-6">
          {block.lines.map((line, itemIndex) => (
            <li key={itemIndex}>{line}</li>
          ))}
        </ul>
      );
    const heading = block.lines.length === 1 ? block.lines[0]?.match(/^(#{1,4})\s+(.*)$/) : null;
    if (heading) {
      const level = heading[1]?.length ?? 2;
      const text = heading[2] ?? '';
      const className = level < 3 ? 'mt-6 mb-2 text-xl font-semibold' : 'mt-5 mb-2 text-lg font-semibold';
      if (level === 1)
        return (
          <h2 key={index} className={className}>
            {text}
          </h2>
        );
      if (level === 2)
        return (
          <h3 key={index} className={className}>
            {text}
          </h3>
        );
      if (level === 3)
        return (
          <h4 key={index} className={className}>
            {text}
          </h4>
        );
      return (
        <h5 key={index} className={className}>
          {text}
        </h5>
      );
    }
    return (
      <p key={index} className="my-3 leading-relaxed">
        {block.lines.join(' ')}
      </p>
    );
  });
}

function findStatementText(item: object): string | null {
  if ('statement' in item && typeof item.statement === 'string') return item.statement;
  if ('claim' in item && typeof item.claim === 'string') return item.claim;
  if ('text' in item && typeof item.text === 'string') return item.text;
  return null;
}

function AuthorValue({ value }: { value: unknown }): React.ReactNode {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) {
    return value.length ? (
      <ul className="m-0 list-disc space-y-2 pl-5">
        {value.map((item, index) => (
          <li key={index}>
            <AuthorValue value={item} />
          </li>
        ))}
      </ul>
    ) : (
      '—'
    );
  }
  if (typeof value !== 'object') return String(value);
  const text = findStatementText(value);
  if (text === null) return <StructuredValue value={value} />;
  if (
    'evidence' in value &&
    (!Array.isArray(value.evidence) || value.evidence.some((kind: unknown) => typeof kind !== 'string'))
  ) {
    return <StructuredValue value={value} />;
  }
  const scope = 'scope' in value && typeof value.scope === 'string' ? value.scope : null;
  const evidence =
    'evidence' in value && Array.isArray(value.evidence)
      ? value.evidence.filter((kind: unknown): kind is string => typeof kind === 'string')
      : [];
  return (
    <>
      {scope ? (
        <Badge variant="outline" className="mr-2 inline-flex align-middle text-xs">
          {scope}
        </Badge>
      ) : null}
      <span>{text}</span>
      {evidence.length ? (
        <ul aria-label="Evidence kinds" className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
          {evidence.map((kind, index) => (
            <li key={index}>{kind.replaceAll('_', ' ')}</li>
          ))}
        </ul>
      ) : null}
    </>
  );
}

function StructuredValue({ value }: { value: unknown }): React.ReactNode {
  if (value === null || value === undefined) return '—';
  if (Array.isArray(value)) {
    return value.length ? (
      <ul className="m-0 list-disc space-y-1 pl-5">
        {value.map((item, index) => (
          <li key={index}>
            <StructuredValue value={item} />
          </li>
        ))}
      </ul>
    ) : (
      '—'
    );
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value);
    return entries.length ? (
      <dl className="grid gap-1 border-l pl-3">
        {entries.map(([name, item]) => (
          <div key={name}>
            <dt className="font-semibold">{name.replaceAll('_', ' ')}</dt>
            <dd className="m-0">
              <StructuredValue value={item} />
            </dd>
          </div>
        ))}
      </dl>
    ) : (
      '—'
    );
  }
  return String(value);
}

export function PlanView({ source, preview }: { source: string; preview: PlanSourceProjection }) {
  const frontmatter = Object.entries(preview.frontmatter);
  const additionalAuthorFields = frontmatter.filter(
    ([name, value]) =>
      !K_PLAN_VIEW_AUTHOR_KEYS.has(name) && !K_PLAN_VIEW_TECHNICAL_KEYS.has(name) && hasMeaningfulValue(value),
  );
  const authorFields = [
    ...K_PLAN_VIEW_AUTHOR_FIELDS.filter(([key]) => key === 'family_id' || Object.hasOwn(preview.frontmatter, key)),
    ...additionalAuthorFields.map(([name]) => [name, name.replaceAll('_', ' ')] as const),
  ];
  const teachingPoints = preview.frontmatter.teaching_points;
  const pointOwners = Array.isArray(teachingPoints)
    ? teachingPoints.flatMap((point: unknown) =>
        typeof point === 'object' && point !== null && 'source_owner' in point ? [point.source_owner] : [],
      )
    : [];
  const topOwner = preview.frontmatter.source_owner;
  const owners = [
    ...new Set([
      ...(Object.hasOwn(preview.frontmatter, 'source_owner') ? (Array.isArray(topOwner) ? topOwner : [topOwner]) : []),
      ...pointOwners,
    ]),
  ];
  const technicalFields = [
    ...frontmatter.filter(
      ([name, value]) =>
        name !== 'source_owner' &&
        (K_PLAN_VIEW_TECHNICAL_KEYS.has(name) || (!K_PLAN_VIEW_AUTHOR_KEYS.has(name) && !hasMeaningfulValue(value))),
    ),
    ...(owners.length ? [['source_owner', owners] as const] : []),
  ];
  const family = preview.frontmatter.family_id;
  const familyValue =
    family === null || family === undefined || (typeof family === 'string' && !family.trim()) ? 'Unassigned' : family;
  return (
    <div className="mt-4 rounded-xl border bg-white p-4 sm:p-5">
      {preview.parseError ? (
        <p role="status" className="mb-3 text-sm text-warning-90">
          Plan frontmatter could not be parsed: {preview.parseError}
        </p>
      ) : null}
      {authorFields.length ? (
        <dl className="grid gap-2 border-b pb-4 sm:grid-cols-[minmax(9rem,.32fr)_minmax(0,1fr)] sm:gap-x-4">
          {authorFields.map(([key, label]) => (
            <div key={key} className="contents">
              <dt className="font-semibold text-secondary-90">{label}</dt>
              <dd className="m-0 min-w-0 text-secondary-80">
                {K_PLAN_VIEW_AUTHOR_KEYS.has(key) ? (
                  <AuthorValue value={key === 'family_id' ? familyValue : preview.frontmatter[key]} />
                ) : (
                  <StructuredValue value={preview.frontmatter[key]} />
                )}
              </dd>
            </div>
          ))}
        </dl>
      ) : null}
      <div className="prose mt-2 max-w-none text-sm">
        {preview.body ? (
          <MarkdownBody source={preview.body} />
        ) : (
          <p className="text-muted-foreground">No plan content.</p>
        )}
      </div>
      {technicalFields.length ? (
        <details className="mt-4 rounded-md border p-3">
          <summary className="cursor-pointer text-sm font-semibold">Technical fields</summary>
          <dl className="mt-2 grid gap-2 sm:grid-cols-[minmax(9rem,.32fr)_minmax(0,1fr)] sm:gap-x-4">
            {technicalFields.map(([name, value]) => (
              <div key={name} className="contents">
                <dt className="font-semibold text-secondary-90">{name.replaceAll('_', ' ')}</dt>
                <dd className="m-0 min-w-0 text-secondary-80">
                  <StructuredValue value={value} />
                </dd>
              </div>
            ))}
          </dl>
        </details>
      ) : null}
      <details className="mt-4 rounded-md border p-3">
        <summary className="cursor-pointer text-sm font-semibold">Raw plan source</summary>
        <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap break-words text-xs">{source}</pre>
      </details>
    </div>
  );
}
