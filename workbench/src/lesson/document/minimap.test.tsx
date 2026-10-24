import { fireEvent, render, screen } from '@testing-library/react';
import * as React from 'react';
import { describe, expect, it, vi } from 'vitest';

import { Minimap } from './minimap';

describe('Lesson minimap', () => {
  it('test_minimap_keyboard_given_focused_lesson_position_expect_scroll_down', () => {
    const scrollRef = React.createRef<HTMLDivElement>();
    const rowElements: React.RefObject<Map<string, HTMLDivElement>> = { current: new Map() };
    render(
      <>
        <div ref={scrollRef} />
        <Minimap scrollRef={scrollRef} rowElements={rowElements} marks={[]} />
      </>,
    );
    const scroller = scrollRef.current;
    if (!scroller) throw new Error('lesson scroller was not rendered');
    Object.defineProperties(scroller, {
      clientHeight: { configurable: true, value: 200 },
      scrollHeight: { configurable: true, value: 1000 },
    });
    const scrollTo = vi.spyOn(scroller, 'scrollTo');
    const minimap = screen.getByRole('scrollbar', { name: 'Lesson position' });
    minimap.focus();

    fireEvent.keyDown(minimap, { key: 'ArrowDown' });

    expect(minimap).toHaveFocus();
    expect(scrollTo).toHaveBeenCalledWith({ top: 40 });
  });
});
