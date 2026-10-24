// Snapshot of flyt-dev frontend/src/components/portable/DialogueView.tsx and frontend/src/pages/LessonDetailPage/components/LessonLayout.tsx @ 6ae9bc2e92df9e50c897dcef0dd2e755cb418080; hand-copied, see knowledge/project/workbench-design-system.md
import * as React from 'react';
import { PlayIcon } from 'lucide-react';

import type {
  LessonPayload,
  PacketElement,
} from '@/api/contracts';
import { Stimulus } from '@/lesson/document/render/exercise';
import { Spans } from '@/lesson/document/render/spans';

type RecordValue = Record<string, unknown>;

function record(value: unknown): RecordValue {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : {};
}

function records(value: unknown): RecordValue[] {
  return Array.isArray(value) ? value.map(record) : [];
}

function Translation({ text, show }: { text: unknown; show: boolean }) {
  if (!show || typeof text !== 'string' || !text) return null;
  return <p lang="en" className="mt-1.5 text-sm text-secondary-80">{text}</p>;
}

function Dialogue({ turns, showGloss }: { turns: RecordValue[]; showGloss: boolean }) {
  const speakers = [...new Set(turns.map((turn) => String(turn.speaker_name ?? turn.speaker ?? '')).filter(Boolean))];
  const alternating = speakers.length === 2;
  return <div aria-label="Dialogue" className="my-3 flex flex-col gap-2.5">
    {turns.map((turn, index) => {
      const speaker = String(turn.speaker_name ?? turn.speaker ?? '');
      const aligned = alternating && speaker === speakers[1];
      return <div key={String(turn.id ?? index)} className={`flex w-full ${aligned ? 'justify-end' : 'justify-start'}`}>
        <div className={`max-w-[85%] rounded-xl border p-3 ${aligned ? 'border-primary-30 bg-primary-10' : 'border-secondary-20 bg-white'}`}>
          {speaker ? <p className="mb-1 text-xs font-semibold tracking-wide text-secondary-80">{speaker}</p> : null}
          <div className="flex items-start gap-2">
            <p lang="nb" className="m-0 min-w-0 flex-1 leading-relaxed"><Spans values={turn.spans ?? turn.text} /></p>
            <button type="button" disabled aria-label={`Audio unavailable for ${speaker || 'this'} line`} title="Audio unavailable in author preview" className="grid size-6 shrink-0 place-items-center text-secondary-80 disabled:cursor-not-allowed"><PlayIcon aria-hidden="true" className="size-3.5" /></button>
          </div>
          <Translation text={turn.translation} show={showGloss} />
        </div>
      </div>;
    })}
  </div>;
}

function ExampleCard({ items, showGloss }: { items: RecordValue[]; showGloss: boolean }) {
  return <div className="my-3 overflow-hidden rounded-xl border border-secondary-20 bg-white">
    {items.map((item, index) => <div key={String(item.id ?? index)} className="border-b border-secondary-10 p-3 last:border-b-0">
      <p lang="nb" className="m-0 font-medium"><Spans values={item.no} /></p>
      {showGloss ? <p lang="en" className="m-0 mt-1 text-sm text-secondary-80"><Spans values={item.en} /></p> : null}
    </div>)}
  </div>;
}

function Block({ block, showGloss }: { block: RecordValue; showGloss: boolean }): React.ReactNode {
  switch (block.kind) {
    case 'paragraph': return <p lang="en" className="my-3 leading-relaxed"><Spans values={block.spans} /></p>;
    case 'heading': return <h4 className="mt-4 mb-2 text-lg font-semibold"><Spans values={block.spans} /></h4>;
    case 'reading': return <Dialogue turns={[block]} showGloss={showGloss} />;
    case 'example': return <ExampleCard items={[block]} showGloss={showGloss} />;
    case 'examples': return <ExampleCard items={records(block.items)} showGloss={showGloss} />;
    case 'list': {
      const items = Array.isArray(block.items) ? block.items : [];
      return block.ordered === true
        ? <ol className="my-3 list-decimal space-y-1 pl-6">{items.map((item, index) => <li key={index}><Spans values={item} /></li>)}</ol>
        : <ul className="my-3 list-disc space-y-1 pl-6">{items.map((item, index) => <li key={index}><Spans values={item} /></li>)}</ul>;
    }
    default: return <div className="my-3 rounded-lg border border-secondary-20 bg-secondary-0 px-3 py-2 text-sm text-secondary-80">Shown in Author view</div>;
  }
}

function ChoiceExercise({ element }: { element: PacketElement }) {
  const payload = record(element.payload);
  const options = records(payload.options);
  return <section aria-label="Exercise" className="rounded-xl border border-secondary-20 bg-white p-4">
    <Stimulus items={element.stimulus} />
    <p className="m-0 font-semibold leading-snug"><Spans values={element.prompt} /></p>
    {payload.stem !== undefined && payload.stem !== null ? <p className="mt-3 rounded-lg border bg-secondary-0 p-3 text-sm font-medium leading-relaxed"><Spans values={payload.stem} /></p> : null}
    <div className="mt-3 space-y-2">
      {options.map((option, index) => <div key={String(option.option_id ?? index)} className="flex items-start gap-2 rounded-lg border border-secondary-50 px-3 py-2.5 text-sm">
        <span aria-hidden="true" className="grid size-6 shrink-0 place-items-center rounded-full bg-primary-20 text-xs font-semibold">{String.fromCharCode(65 + index)}</span>
        <span>{String(option.text ?? '')}</span>
      </div>)}
    </div>
  </section>;
}

function LearnerElement({ element, showGloss }: { element: PacketElement; showGloss: boolean }) {
  if (element.kind === 'section') {
    const blocks = records(element.blocks);
    const rendered: React.ReactNode[] = [];
    for (let index = 0; index < blocks.length;) {
      const block = blocks[index];
      if (block?.kind === 'reading' && block.dialogue_id) {
        const dialogueId = block.dialogue_id;
        const turns: RecordValue[] = [];
        while (blocks[index]?.kind === 'reading' && blocks[index]?.dialogue_id === dialogueId) {
          turns.push(blocks[index] as RecordValue);
          index += 1;
        }
        rendered.push(<Dialogue key={`dialogue-${String(dialogueId)}-${index}`} turns={turns} showGloss={showGloss} />);
      } else {
        rendered.push(<Block key={`block-${index}`} block={block ?? {}} showGloss={showGloss} />);
        index += 1;
      }
    }
    return <section key={String(element.id)} aria-label={String(element.title ?? 'Lesson section')} className="rounded-xl border border-primary-20 bg-white p-4">
      <h2 className="m-0 text-base font-semibold">{String(element.title ?? '')}</h2>
      <div className="mt-2">{rendered}</div>
    </section>;
  }
  if (element.kind === 'exercise' && element.operation === 'choose' && records(record(element.payload).options).length > 0) return <ChoiceExercise key={element.id} element={element} />;
  return <div key={element.id} className="rounded-lg border border-secondary-20 bg-secondary-0 px-3 py-2.5 text-sm text-secondary-80">Shown in Author view</div>;
}

export function LearnerView({ lesson }: { lesson: LessonPayload }) {
  const [showGloss, setShowGloss] = React.useState(false);
  const title = lesson.lesson?.title ?? lesson.lessonId;
  const elements = lesson.elements as PacketElement[];
  return <section aria-label="Learner view" className="mx-auto max-w-[43rem] px-4 pt-6 pb-12 sm:px-8">
    <div className="mb-3 text-center">
      <p className="m-0 text-xs font-semibold tracking-[0.16em] text-secondary-80 uppercase">Learner view</p>
      <p className="mt-1 text-sm text-secondary-80">Rendered with Flyt lesson components. Read-only; Flyt owns presentation.</p>
      <button type="button" aria-pressed={showGloss} onClick={() => setShowGloss((value) => !value)} className={`mt-2 rounded-full px-4 py-2 text-sm font-semibold focus-visible:ring-2 focus-visible:ring-ring ${showGloss ? 'bg-secondary-100 text-white' : 'border border-secondary-50 bg-white text-secondary-100'}`}>
        English gloss {showGloss ? 'on' : 'off'}
      </button>
    </div>
    <div className="mx-auto max-w-[376px] overflow-hidden rounded-[26px] border border-secondary-50 bg-secondary-0 shadow-sm">
      <header className="flex items-center justify-between border-b border-secondary-20 bg-white px-4 py-3">
        <strong className="text-xl">Flyt</strong>
        <span aria-hidden="true" className="flex gap-2 text-primary-80"><span className="grid size-8 place-items-center rounded-lg border border-primary-30 bg-primary-10">✧</span><span className="grid size-8 place-items-center rounded-full border border-primary-30">◎</span></span>
      </header>
      <div className="max-h-[min(66vh,720px)] space-y-3 overflow-y-auto p-3">
        <section className="rounded-xl border border-primary-20 bg-white p-4">
          <span className="text-xs text-secondary-80">Lesson</span>
          <h1 className="mt-1 mb-1 text-xl leading-tight font-semibold">{title}</h1>
          <p className="m-0 text-xs text-secondary-80">{lesson.lesson?.cefrLevel ?? 'Lesson'} · {elements.filter((element) => element.kind === 'section').length} sections</p>
        </section>
        {elements.map((element, index) => <LearnerElement key={`${element.id ?? element.kind}-${index}`} element={element} showGloss={showGloss} />)}
      </div>
    </div>
  </section>;
}
