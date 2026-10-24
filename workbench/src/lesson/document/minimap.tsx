import * as React from 'react';

import { cn } from '@/lib/utils';

export interface MinimapMark {
  rowId: string;
  kind: string;
  flag: 'warning' | 'error' | null;
  hasProposal: boolean;
  label: string | null;
}

interface Measured extends MinimapMark {
  top: number;
  height: number;
}

export interface MinimapProps {
  scrollRef: React.RefObject<HTMLDivElement | null>;
  rowElements: React.RefObject<Map<string, HTMLDivElement>>;
  marks: MinimapMark[];
}

export function Minimap({ scrollRef, rowElements, marks }: MinimapProps) {
  const mapRef = React.useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = React.useState<Measured[]>([]);
  const [viewport, setViewport] = React.useState({ top: 0, height: 0 });
  const [position, setPosition] = React.useState(0);
  const [scale, setScale] = React.useState(0);
  const scroller = scrollRef.current;
  const maxScrollTop = scroller ? Math.max(0, scroller.scrollHeight - scroller.clientHeight) : 0;

  const measure = React.useCallback(() => {
    const scroller = scrollRef.current;
    const map = mapRef.current;
    if (!scroller || !map) return;
    const nextScale = map.clientHeight / Math.max(scroller.scrollHeight, 1);
    const origin = scroller.getBoundingClientRect().top - scroller.scrollTop;
    setScale(nextScale);
    setMeasured(
      marks.flatMap((mark) => {
        const rect = rowElements.current.get(mark.rowId)?.getBoundingClientRect();
        return rect?.height
          ? [{ ...mark, top: (rect.top - origin) * nextScale, height: Math.max(2, rect.height * nextScale - 1) }]
          : [];
      }),
    );
    setViewport({ top: scroller.scrollTop * nextScale, height: scroller.clientHeight * nextScale });
    setPosition(scroller.scrollTop);
  }, [marks, rowElements, scrollRef]);

  React.useLayoutEffect(() => {
    measure();
    const scroller = scrollRef.current;
    if (!scroller) return;
    const observer = new ResizeObserver(measure);
    observer.observe(scroller);
    if (scroller.firstElementChild) observer.observe(scroller.firstElementChild);
    const onScroll = () => {
      setViewport({ top: scroller.scrollTop * scale, height: scroller.clientHeight * scale });
      setPosition(scroller.scrollTop);
    };
    scroller.addEventListener('scroll', onScroll, { passive: true });
    return () => {
      observer.disconnect();
      scroller.removeEventListener('scroll', onScroll);
    };
  }, [measure, scale, scrollRef]);

  const scrollTo = (clientY: number) => {
    const scroller = scrollRef.current;
    const map = mapRef.current;
    if (!scroller || !map) return;
    const ratio = (clientY - map.getBoundingClientRect().top) / map.clientHeight;
    scroller.scrollTo({ top: ratio * scroller.scrollHeight - scroller.clientHeight / 2 });
  };

  const scrollByKeyboard = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (!scroller) return;
    const page = scroller.clientHeight;
    const step = 40;
    const currentMaxScrollTop = Math.max(0, scroller.scrollHeight - page);
    let nextPosition: number;
    switch (event.key) {
      case 'ArrowDown':
        nextPosition = position + step;
        break;
      case 'ArrowUp':
        nextPosition = position - step;
        break;
      case 'PageDown':
        nextPosition = position + page;
        break;
      case 'PageUp':
        nextPosition = position - page;
        break;
      case 'Home':
        nextPosition = 0;
        break;
      case 'End':
        nextPosition = currentMaxScrollTop;
        break;
      default:
        return;
    }
    event.preventDefault();
    scroller.scrollTo({ top: Math.max(0, Math.min(nextPosition, currentMaxScrollTop)) });
  };

  return (
    <div
      ref={mapRef}
      role="scrollbar"
      aria-label="Lesson position"
      aria-controls="lesson-document-scroll"
      aria-orientation="vertical"
      aria-valuemin={0}
      aria-valuemax={maxScrollTop}
      aria-valuenow={Math.min(position, maxScrollTop)}
      aria-valuetext={`${maxScrollTop ? Math.round((position / maxScrollTop) * 100) : 0}% through lesson`}
      tabIndex={0}
      data-testid="minimap"
      className="relative w-9 shrink-0 cursor-pointer touch-none overflow-hidden border-l bg-secondary-0 select-none focus-visible:z-20 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring sm:w-14 md:w-22"
      onKeyDown={scrollByKeyboard}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId);
        scrollTo(event.clientY);
      }}
      onPointerMove={(event) => {
        if (event.currentTarget.hasPointerCapture(event.pointerId)) scrollTo(event.clientY);
      }}
    >
      {measured.map((mark) => (
        <React.Fragment key={mark.rowId}>
          <i
            aria-hidden="true"
            className={cn(
              'pointer-events-none absolute right-2.5 left-3.5 rounded-[1px] bg-secondary-30',
              mark.kind === 'heading' && 'right-6 left-1.5 bg-secondary-90',
              mark.kind === 'exercise' && 'bg-primary-40',
              mark.flag && 'right-1 left-1',
              mark.flag === 'warning' && 'bg-warning-50',
              mark.flag === 'error' && 'bg-destructive-60',
              mark.hasProposal && 'right-1 left-1 bg-primary-70',
            )}
            style={{ top: mark.top, height: mark.height }}
          />
          {mark.label ? (
            <span
              aria-hidden="true"
              className="pointer-events-none absolute right-1 left-1.5 -translate-y-[110%] truncate text-[0.55rem] leading-tight text-muted-foreground"
              style={{ top: mark.top }}
            >
              {mark.label}
            </span>
          ) : null}
        </React.Fragment>
      ))}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 z-10 border-y border-primary-60 bg-primary-60/15"
        style={{ top: viewport.top, height: viewport.height }}
      />
    </div>
  );
}
