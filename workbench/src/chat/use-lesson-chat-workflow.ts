/** Entry point: `useLessonChatWorkflow`. */

import * as React from 'react';
import { toast } from 'sonner';
import type { FindingData, LessonPayload } from '@/api/contracts';
import type { ChatTurnData, ContextChipData } from '@/chat/types';
import { useWorkbenchJob } from '@/jobs/use-workbench-job';
import { buildTargetKey } from '@/lesson/document/rows';

const K_LESSON_CHAT_CONTEXT_OPTIONS = [
  'lesson.md',
  'plan.md',
  'exercises.yaml',
  'Current section',
  'Open findings',
  'Curriculum entry',
];

interface LessonChatRow {
  rowId: string;
  sectionLabel: string | null;
  targetKey: string;
}

export interface LessonChatWorkflowOptions {
  lessonId: string;
  chatHistory: LessonPayload['state']['chat'];
  activeTurn: LessonPayload['state']['activeTurn'];
  rows: LessonChatRow[];
  currentSectionKey: () => string | null | undefined;
}

export function useLessonChatWorkflow({
  lessonId,
  chatHistory,
  activeTurn,
  rows,
  currentSectionKey,
}: LessonChatWorkflowOptions) {
  const chat = useWorkbenchJob(['lesson', lessonId], chatHistory, activeTurn);
  const [pendingTurn, setPendingTurn] = React.useState<ChatTurnData | null>(null);
  const [composerChips, setComposerChips] = React.useState<ContextChipData[]>([]);
  const inputRef = React.useRef<HTMLTextAreaElement>(null);

  React.useEffect(() => {
    if (chat.isSuccess) setPendingTurn(null);
  }, [chat.isSuccess]);

  const addSelection = React.useCallback(() => {
    const selection = window.getSelection();
    const text = selection?.toString() ?? '';
    const anchor = selection?.anchorNode?.parentElement?.closest<HTMLElement>('[data-row-kind]');
    const row = rows.find((candidate) => candidate.rowId === anchor?.id);
    if (!text.trim() || !row) {
      toast('Select lesson text first, then press Ctrl+L or Add to chat.');
      return;
    }
    setComposerChips((chips) => [
      ...chips,
      {
        kind: 'selection',
        label: row.sectionLabel || 'Lesson',
        detail: text,
        key: row.targetKey,
      },
    ]);
    inputRef.current?.focus();
  }, [rows]);

  const askChat = React.useCallback((finding: FindingData) => {
    const readable = [
      `${finding.origin === 'ai' ? finding.level : finding.severity} · ${finding.message || finding.explanation}`,
      finding.excerpt ? `Quoted source: ${finding.excerpt}` : '',
      finding.quoteEnglish ? `English gloss: ${finding.quoteEnglish}` : '',
      finding.suggestedChange ? `Suggested change: ${finding.suggestedChange}` : '',
    ]
      .filter(Boolean)
      .join('\n');
    setComposerChips((chips) => [
      ...chips,
      {
        kind: 'finding',
        label: finding.label ?? 'Review finding',
        detail: readable,
        key: buildTargetKey(finding.target),
        referenceId: finding.findingId,
      },
    ]);
    inputRef.current?.focus();
  }, []);

  const onFix = React.useCallback(
    (finding: FindingData) =>
      chat.mutate({
        route: `/api/lessons/${encodeURIComponent(lessonId)}/findings/${encodeURIComponent(finding.findingId)}/fix`,
        body: {},
      }),
    [chat.mutate, lessonId],
  );

  const onExplain = React.useCallback(
    (blockId: string) =>
      chat.mutate({ route: `/api/lessons/${encodeURIComponent(lessonId)}/explain`, body: { block_id: blockId } }),
    [chat.mutate, lessonId],
  );

  const addContextChip = React.useCallback(
    (chip: ContextChipData) =>
      setComposerChips((chips) => [
        ...chips,
        chip.label === 'Current section' ? { ...chip, key: currentSectionKey()?.split('#')[0] } : chip,
      ]),
    [currentSectionKey],
  );

  const removeContextChip = React.useCallback(
    (index: number) => setComposerChips((chips) => chips.filter((_, chipIndex) => chipIndex !== index)),
    [],
  );

  const sendChat = React.useCallback(
    (text: string) => {
      const chips = composerChips;
      const message = text || 'Use the attached context.';
      setPendingTurn({ role: 'author', text: message, chips, toolNote: null });
      setComposerChips([]);
      chat.mutate(
        { route: `/api/lessons/${encodeURIComponent(lessonId)}/chat`, body: { message, chips } },
        { onSuccess: () => setPendingTurn(null), onError: () => setPendingTurn(null) },
      );
    },
    [chat.mutate, composerChips, lessonId],
  );

  return {
    chat,
    pendingTurn,
    composerChips,
    inputRef,
    contextOptions: K_LESSON_CHAT_CONTEXT_OPTIONS,
    addSelection,
    askChat,
    onFix,
    onExplain,
    addContextChip,
    removeContextChip,
    sendChat,
  };
}
