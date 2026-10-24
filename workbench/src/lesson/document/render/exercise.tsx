import type * as React from 'react';

import { Spans } from './spans';

type Data = Record<string, unknown>;

const PANEL_LABEL = 'block text-[0.68rem] font-semibold tracking-[0.16em] text-secondary-80 uppercase';

function list(value: unknown): Data[] {
  return Array.isArray(value) ? (value as Data[]) : [];
}

function text(value: unknown): string {
  return value == null ? '' : String(value);
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="text-sm leading-relaxed">
      <strong className={PANEL_LABEL}>{title}</strong>
      {children}
    </div>
  );
}

function Disclosure({ summary, children }: { summary: string; children: React.ReactNode }) {
  return (
    <details className="mt-3">
      <summary className="w-fit cursor-pointer text-xs font-semibold text-secondary-80">{summary}</summary>
      <div className="mt-2">{children}</div>
    </details>
  );
}

export function Stimulus({ items }: { items: unknown }) {
  return list(items)
    .filter((item) => item.kind === 'dialogue')
    .map((item, index) => (
      // biome-ignore lint/a11y/useSemanticElements: Preserve the existing named dialogue group.
      <div
        key={index}
        role="group"
        aria-label="Dialogue"
        className="my-4 flex flex-col gap-2.5 rounded-xl border bg-card p-4"
      >
        {list(item.turns).map((turn, turnIndex) => (
          <div
            key={turnIndex}
            className="w-fit max-w-[85%] rounded-lg border bg-secondary-0 p-3 even:self-end even:border-primary-30 even:bg-primary-10"
          >
            <span className="mb-1 block text-xs font-semibold tracking-[0.16em] text-muted-foreground uppercase">
              {text(turn.speaker)}
            </span>
            <div lang="nb" className="leading-7">
              <Spans values={turn.text} />
            </div>
          </div>
        ))}
      </div>
    ));
}

function textOf(items: Data[], key: string, id: unknown, fallback: unknown): string {
  return text(items.find((item) => item[key] === id)?.text ?? fallback);
}

export function ExercisePayload({ operation, data }: { operation: string; data: Data }): React.ReactNode {
  switch (operation) {
    case 'choose':
      return (
        <Panel title="Author answer">
          {data.stem ? (
            <div lang="en" className="my-3 rounded-lg border bg-secondary-0 p-4 text-lg font-semibold">
              <span className={`mb-1.5 ${PANEL_LABEL}`}>Question stem</span>
              <Spans values={data.stem} />
            </div>
          ) : null}
          <ul className="mt-3 flex flex-col gap-2">
            {list(data.options).map((option, index) => {
              const isAnswer = option.option_id === data.answer_id;
              return (
                <li
                  key={index}
                  data-answer={isAnswer || undefined}
                  className={`rounded-lg border-2 p-3 text-base ${isAnswer ? 'border-accent-20 bg-accent-0 text-accent-90' : 'bg-card'}`}
                >
                  {text(option.text)}
                  {isAnswer ? ' · Answer' : ''}
                  {option.why ? (
                    <small lang="en" className="block text-sm opacity-90">
                      Why: {text(option.why)}
                    </small>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </Panel>
      );
    case 'recall_fill':
      return (
        <Panel title="Author answer · fill order">
          <p className="mt-2 text-base">
            {list(data.segments).map((item, index) => {
              if (item.kind === 'span') return <Spans key={index} values={item.spans} />;
              const options = Array.isArray(item.options) ? item.options.map(text) : [];
              const answerIndex = typeof item.answer_index === 'number' ? item.answer_index : -1;
              return (
                <span key={index}>
                  <mark lang="nb" className="rounded bg-primary-10 px-1.5 text-primary-90">
                    {text(item.blank_id ?? 'blank')} = {options[answerIndex] ?? ''}
                  </mark>
                  <small lang="nb" className="ml-1 text-muted-foreground">
                    Options: {options.join(' · ')}
                  </small>
                </span>
              );
            })}
          </p>
        </Panel>
      );
    case 'match_pairs':
      return (
        <Panel title="Author answer · pairs">
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {list(data.pairs).map((pair, index) => (
              <li key={index}>
                {textOf(list(data.left), 'left_id', pair.left_id, pair.left_id)} →{' '}
                {textOf(list(data.right), 'right_id', pair.right_id, pair.right_id)}
              </li>
            ))}
          </ul>
          <Disclosure summary="All authored sides">
            <span className="text-muted-foreground">Left</span>
            <ul className="mb-2 list-disc pl-5">
              {list(data.left).map((item, index) => (
                <li key={index}>{text(item.text)}</li>
              ))}
            </ul>
            <span className="text-muted-foreground">Right</span>
            <ul className="list-disc pl-5">
              {list(data.right).map((item, index) => (
                <li key={index}>{text(item.text)}</li>
              ))}
            </ul>
          </Disclosure>
        </Panel>
      );
    case 'judge':
      return (
        <Panel title={`Author judgment · ${data.is_correct ? 'correct' : 'incorrect'}`}>
          <p lang="nb" className="mt-2">
            <Spans values={data.sentence} />
          </p>
          <p lang="en">{text(data.feedback) || 'No feedback authored'}</p>
        </Panel>
      );
    case 'categorize': {
      const buckets = list(data.buckets);
      return (
        <Panel title="Author answer · categories">
          <ul className="mt-2 list-disc space-y-1 pl-5">
            {list(data.items).map((item, index) => (
              <li key={index}>
                {text(item.text)} →{' '}
                {text(buckets.find((bucket) => bucket.bucket_id === item.bucket_id)?.label ?? item.bucket_id)}
              </li>
            ))}
          </ul>
        </Panel>
      );
    }
    case 'build': {
      const tokens = list(data.tokens);
      const order = Array.isArray(data.answer_order) ? data.answer_order : [];
      return (
        <Panel title="Author answer · build order">
          <p lang="nb" className="mt-2">
            {order.map((tokenId) => textOf(tokens, 'token_id', tokenId, tokenId)).join(' ')}
          </p>
          <Disclosure summary="Authored token bank">
            <p lang="nb">{tokens.map((token) => `${text(token.text)}${token.fixed ? ' [fixed]' : ''}`).join(' ')}</p>
          </Disclosure>
        </Panel>
      );
    }
    case 'find_fix':
      return (
        <Panel title="Original sentence · error marked">
          <p lang="nb" className="mt-2">
            {list(data.tokens)
              .map((token) => `${text(token.text)}${token.token_id === data.error_token_id ? ' [error]' : ''}`)
              .join(' ')}
          </p>
          <p lang="en">
            <strong>Authored feedback</strong>
            <br />
            {text(data.feedback)}
          </p>
        </Panel>
      );
    case 'write':
      return (
        <Panel title="Author rubric">
          <p className="mt-2">
            {(['response_language', 'min_words', 'max_words'] as const)
              .filter((key) => data[key] != null)
              .map((key) => `${key.replaceAll('_', ' ')}: ${text(data[key])}`)
              .join(' · ')}
          </p>
          <p lang="en">{text(data.judge_prompt)}</p>
          <ul className="list-disc space-y-1 pl-5">
            {list(data.criteria).map((item, index) => (
              <li key={index}>
                <strong>{text(item.id)}</strong> {text(item.instruction)}
              </li>
            ))}
          </ul>
        </Panel>
      );
    default:
      return (
        <Panel title="Author payload">
          <pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-secondary-0 p-3 font-mono text-xs whitespace-pre-wrap">
            {JSON.stringify(data, null, 2)}
          </pre>
        </Panel>
      );
  }
}
