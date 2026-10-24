/** Entry point: `ProposalDiff` renders a bounded comparison of proposal text. */

import * as React from 'react';

import type { ProposalText } from '@/api/contracts';

const K_PROPOSAL_DIFF_MAX_WORD_COMPARISONS = 200_000;
const K_PROPOSAL_DIFF_MAX_LINE_COMPARISONS = 40_000;

interface WordMatches {
  before: Set<number>;
  after: Set<number>;
}

interface ProposalComparison {
  matches: WordMatches;
}

export function ProposalDiff({ before, after }: { before: ProposalText[]; after: ProposalText[] }) {
  const beforeText = before.map((region) => region.text).join('\n');
  const afterText = after.map((region) => region.text).join('\n');
  const comparison = React.useMemo(() => compareText(beforeText, afterText), [afterText, beforeText]);
  const { matches } = comparison;
  return (
    <div className="mt-2.5 grid grid-cols-2 gap-3">
      {[
        { label: 'Before', regions: before, matches: matches.before, removed: true },
        { label: 'After', regions: after, matches: matches.after, removed: false },
      ].map(({ label, regions, matches: regionMatches, removed }) => {
        let position = 0;
        return (
          <div key={label} className="min-w-0">
            <span className="mb-1 block text-[0.68rem] font-semibold tracking-widest text-secondary-80 uppercase">
              {label}
            </span>
            <section
              aria-label={`Rendered ${label.toLowerCase()}`}
              className="rounded-md border bg-card px-2.5 py-2 text-sm whitespace-pre-wrap break-words"
            >
              {regions.map((region, regionIndex) => (
                <p key={regionIndex} lang={region.lang} className="mb-1 last:mb-0">
                  {region.text.split(/(\s+)/).map((word, index) => {
                    if (!word.trim()) return word;
                    const changed = !regionMatches.has(position++);
                    return changed ? (
                      removed ? (
                        <del key={index} className="bg-destructive-10 text-destructive-90 decoration-destructive-60">
                          {word}
                        </del>
                      ) : (
                        <ins key={index} className="bg-accent-20 text-accent-100 no-underline">
                          {word}
                        </ins>
                      )
                    ) : (
                      word
                    );
                  })}
                </p>
              ))}
            </section>
          </div>
        );
      })}
    </div>
  );
}

function compareText(beforeText: string, afterText: string): ProposalComparison {
  const beforeWords = words(beforeText);
  const afterWords = words(afterText);
  const comparisons = beforeWords.length * afterWords.length;
  const matches =
    comparisons <= K_PROPOSAL_DIFF_MAX_WORD_COMPARISONS
      ? commonTokens(beforeWords, afterWords)
      : commonLines(beforeText, afterText);
  return { matches };
}

function commonLines(beforeText: string, afterText: string): WordMatches {
  const beforeLines = beforeText.split(/\r?\n/);
  const afterLines = afterText.split(/\r?\n/);
  const lineMatches =
    beforeLines.length * afterLines.length <= K_PROPOSAL_DIFF_MAX_LINE_COMPARISONS
      ? commonTokens(beforeLines, afterLines)
      : matchingEqualLines(beforeLines, afterLines);
  const matches: WordMatches = { before: new Set(), after: new Set() };
  const beforeOffsets = lineWordOffsets(beforeLines);
  const afterOffsets = lineWordOffsets(afterLines);
  for (const beforeLineIndex of lineMatches.before) {
    const afterLineIndex = [...lineMatches.after].find((index) => {
      if (index < 0) return false;
      return beforeLines[beforeLineIndex] === afterLines[index];
    });
    if (afterLineIndex === undefined) continue;
    lineMatches.after.delete(afterLineIndex);
    const lineWordCount = words(beforeLines[beforeLineIndex]).length;
    for (let wordIndex = 0; wordIndex < lineWordCount; wordIndex += 1) {
      matches.before.add(beforeOffsets[beforeLineIndex] + wordIndex);
      matches.after.add(afterOffsets[afterLineIndex] + wordIndex);
    }
  }
  return matches;
}

function lineWordOffsets(lines: string[]): number[] {
  let offset = 0;
  return lines.map((line) => {
    const currentOffset = offset;
    offset += words(line).length;
    return currentOffset;
  });
}

function matchingEqualLines(before: string[], after: string[]): WordMatches {
  const afterIndexes = new Map<string, number[]>();
  after.forEach((line, index) => {
    const indexes = afterIndexes.get(line);
    if (indexes) indexes.push(index);
    else afterIndexes.set(line, [index]);
  });
  const cursors = new Map<string, number>();
  const matches: WordMatches = { before: new Set(), after: new Set() };
  let lastAfterIndex = -1;
  before.forEach((line, beforeIndex) => {
    const indexes = afterIndexes.get(line) ?? [];
    let cursor = cursors.get(line) ?? 0;
    while (cursor < indexes.length && indexes[cursor] <= lastAfterIndex) cursor += 1;
    cursors.set(line, cursor + 1);
    const afterIndex = indexes[cursor];
    if (afterIndex === undefined) return;
    matches.before.add(beforeIndex);
    matches.after.add(afterIndex);
    lastAfterIndex = afterIndex;
  });
  return matches;
}

function commonTokens(before: string[], after: string[]): WordMatches {
  const lengths = Array.from({ length: before.length + 1 }, () => new Uint32Array(after.length + 1));
  for (let left = before.length - 1; left >= 0; left--) {
    for (let right = after.length - 1; right >= 0; right--) {
      lengths[left][right] =
        before[left] === after[right]
          ? lengths[left + 1][right + 1] + 1
          : Math.max(lengths[left + 1][right], lengths[left][right + 1]);
    }
  }
  const matches: WordMatches = { before: new Set(), after: new Set() };
  let left = 0;
  let right = 0;
  while (left < before.length && right < after.length) {
    if (before[left] === after[right]) {
      matches.before.add(left++);
      matches.after.add(right++);
    } else if (lengths[left + 1][right] >= lengths[left][right + 1]) left++;
    else right++;
  }
  return matches;
}

function words(text: string): string[] {
  return text.split(/\s+/).filter(Boolean);
}
