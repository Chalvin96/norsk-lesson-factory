import type * as React from 'react';

import { Spans } from './spans';

export type Block = Record<string, unknown> & { kind?: string };

const LABEL = 'text-[0.68rem] font-semibold tracking-[0.16em] uppercase';

function getCellLanguage(langs: unknown, index: number): string {
  return Array.isArray(langs) && typeof langs[index] === 'string' ? (langs[index] as string) : 'en';
}

function list(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? (value as Record<string, unknown>[]) : [];
}

function Example({ no, en, teachingRole }: { no: unknown; en: unknown; teachingRole?: unknown }) {
  const role = teachingRole === 'incorrect' || teachingRole === 'caution' ? teachingRole : 'model';
  return (
    <div className="grid grid-cols-[2rem_minmax(0,1fr)] gap-x-3 px-3 py-2.5 [counter-increment:example-row] before:row-span-3 before:grid before:size-7 before:place-items-center before:rounded-full before:bg-primary-0 before:text-[0.6rem] before:font-semibold before:text-primary-80 before:content-[counter(example-row,decimal-leading-zero)] [&+&]:border-t">
      <div className="col-start-2 flex min-h-5 items-center justify-end">
        {role === 'incorrect' ? (
          <span role="img" aria-label="Incorrect example" className="text-xs font-semibold text-destructive-80">
            ✗ Incorrect
          </span>
        ) : role === 'caution' ? (
          <span role="img" aria-label="Caution example" className="text-xs font-semibold text-warning-90">
            ! Caution
          </span>
        ) : (
          <span role="img" aria-label="Correct example" className="text-xs font-semibold text-primary-90">
            ✓
          </span>
        )}
      </div>
      <div lang="nb" className="col-start-2 font-medium">
        <Spans values={no} />
      </div>
      <div lang="en" className="col-start-2 text-sm text-muted-foreground">
        <Spans values={en} />
      </div>
    </div>
  );
}

export function BlockView({ block, afterReading }: { block: Block; afterReading?: React.ReactNode }): React.ReactNode {
  switch (block.kind) {
    case 'heading': {
      const Heading = block.level === 3 ? 'h3' : 'h4';
      return (
        <Heading lang="en" className="mt-5 mb-2 text-lg font-semibold">
          <Spans values={block.spans} />
        </Heading>
      );
    }
    case 'paragraph':
      return (
        <p lang="en" className="my-3 leading-relaxed">
          <Spans values={block.spans} />
        </p>
      );
    case 'reading':
      return (
        <div className="my-4 rounded-xl border bg-card p-4 shadow-sm">
          {block.speaker_name ? (
            <span className={`mb-1.5 block text-muted-foreground ${LABEL}`}>{String(block.speaker_name)}</span>
          ) : null}
          <div lang="nb" className="leading-7 font-medium">
            <Spans values={block.spans} />
          </div>
          <div lang="en" className="mt-1.5 text-sm text-muted-foreground">
            {String(block.translation ?? '')}
          </div>
          {afterReading}
        </div>
      );
    case 'list': {
      const List = block.ordered ? 'ol' : 'ul';
      return (
        <List lang="en" className={`my-3 space-y-1.5 pl-6 ${block.ordered ? 'list-decimal' : 'list-disc'}`}>
          {(Array.isArray(block.items) ? block.items : []).map((item, index) => (
            <li key={index} lang="en">
              <Spans values={item} />
            </li>
          ))}
        </List>
      );
    }
    case 'rule':
      return (
        <div lang="en" className="my-4 rounded-xl border-l-4 border-primary-70 bg-primary-0 p-3 text-sm">
          <span className={`mb-1 block text-primary-80 ${LABEL}`}>Rule</span>
          <Spans values={block.statement} />
        </div>
      );
    case 'example':
      return (
        <div className="my-4 overflow-hidden rounded-xl border bg-card [counter-reset:example-row]">
          <Example no={block.no} en={block.en} teachingRole={block.teaching_role} />
        </div>
      );
    case 'examples':
      return (
        <div className="my-4 overflow-hidden rounded-xl border bg-card [counter-reset:example-row]">
          {list(block.items).map((item, index) => (
            <Example key={index} no={item.no} en={item.en} teachingRole={item.teaching_role} />
          ))}
        </div>
      );
    case 'word_list':
      return (
        <dl className="my-4 grid grid-cols-[minmax(0,0.85fr)_minmax(0,1.15fr)] overflow-hidden rounded-xl border text-sm">
          {list(block.items).map((item, index) => (
            <div key={index} className="contents">
              <dt lang="nb" className="border-b bg-secondary-10 px-3 py-2.5 font-semibold">
                {String(item.term ?? '')}
              </dt>
              <dd lang="en" className="m-0 border-b px-3 py-2.5 text-secondary-80">
                {String(item.form ?? '')}
              </dd>
            </div>
          ))}
        </dl>
      );
    case 'table': {
      const langs = block.col_langs;
      return (
        <div className="my-4 overflow-x-auto rounded-xl border bg-card">
          <span className="block border-b px-3 py-2.5 text-sm text-muted-foreground">
            Table · {Array.isArray(langs) ? langs.join(' · ') : ''}
          </span>
          <table className="w-full min-w-[28rem] border-collapse text-sm">
            <thead>
              <tr>
                {(Array.isArray(block.headers) ? block.headers : []).map((cell, index) => (
                  <th
                    key={index}
                    scope="col"
                    lang={getCellLanguage(langs, index)}
                    className="bg-secondary-10 px-3 py-2.5 text-left font-semibold"
                  >
                    <Spans values={cell} />
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(Array.isArray(block.rows) ? block.rows : []).map((row, rowIndex) => (
                <tr key={rowIndex}>
                  {(Array.isArray(row) ? row : []).map((cell, index) => (
                    <td key={index} lang={getCellLanguage(langs, index)} className="border-t px-3 py-2.5">
                      <Spans values={cell} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
    case 'callout': {
      const isWarning = block.level === 'warning';
      return (
        <aside
          lang="en"
          className={`my-4 rounded-xl border p-3 ${isWarning ? 'border-warning-50 bg-warning-10 text-warning-90' : 'border-primary-20 bg-primary-10'}`}
        >
          <strong className={`mb-1.5 block ${LABEL}`}>{String(block.level ?? 'note')}</strong>
          <BlockList blocks={block.blocks} />
        </aside>
      );
    }
    default:
      return <div className="my-2 text-sm text-destructive-70">Unsupported block kind: {String(block.kind)}</div>;
  }
}

export function BlockList({ blocks }: { blocks: unknown }) {
  return (Array.isArray(blocks) ? (blocks as Block[]) : []).map((block, index) => (
    <BlockView key={index} block={block} />
  ));
}
