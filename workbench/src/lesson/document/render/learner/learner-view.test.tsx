import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { lessonPayload, renderAt } from '@/test/fixtures';

async function openLearnerView() {
  await userEvent.click(screen.getByRole('button', { name: 'More lesson actions' }));
  await userEvent.click(screen.getByRole('menuitem', { name: 'Learner view' }));
}

describe('Learner view', () => {
  it('test_learner_view_given_overflow_navigation_and_gloss_toggle_expect_translations_follow_toggle', async () => {
    renderAt('/lessons/demo', { lesson: lessonPayload() });
    await openLearnerView();

    expect(await screen.findByRole('button', { name: 'English gloss off' })).toHaveAttribute('aria-pressed', 'false');
    expect(screen.queryByText("I'm making coffee.")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'English gloss off' }));
    expect(await screen.findByRole('button', { name: 'English gloss on' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText("I'm making coffee.")).toBeInTheDocument();
  });

  it('test_choice_exercise_given_stimulus_and_stem_expect_both_before_options', async () => {
    const lesson = lessonPayload({
      elements: [{
        kind: 'exercise',
        id: 'recognize_proposal',
        operation: 'choose',
        prompt: [{ kind: 'text', value: 'Choose the meaning.' }],
        stimulus: [{ kind: 'dialogue', turns: [{ speaker: 'Mina', text: [{ kind: 'text', value: 'Skal vi gå en tur?' }] }] }],
        payload: {
          stem: [{ kind: 'text', value: 'What does Mina suggest?' }],
          options: [{ option_id: 'walk', text: 'She suggests a walk.' }],
        },
      }],
    });
    renderAt('/lessons/demo', { lesson });
    await openLearnerView();

    const exercise = screen.getByRole('region', { name: 'Exercise' });
    const stimulus = screen.getByText('Skal vi gå en tur?');
    const stem = screen.getByText('What does Mina suggest?');
    const option = screen.getByText('She suggests a walk.');
    expect(exercise).toContainElement(stimulus);
    expect(stimulus.compareDocumentPosition(stem) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(stem.compareDocumentPosition(option) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('test_ordered_list_given_span_array_items_expect_numbered_readable_items', async () => {
    const lesson = lessonPayload({
      elements: [{
        kind: 'section',
        id: 'a-plan-for-saturday',
        title: 'Agree on a plan',
        blocks: [{
          kind: 'list',
          ordered: true,
          items: [
            [{ kind: 'text', value: 'Mina proposes an activity.' }],
            [{ kind: 'text', value: 'Jonas responds positively but asks about the time.' }],
            [{ kind: 'text', value: 'Mina offers an alternative.' }],
            [{ kind: 'text', value: 'They state the final plan clearly.' }],
          ],
        }],
      }],
    });
    renderAt('/lessons/demo', { lesson });
    await openLearnerView();

    const lists = within(screen.getByRole('region', { name: 'Learner view' })).getAllByRole('list');
    const list = lists.find((item) => item.tagName === 'UL' || item.tagName === 'OL');
    if (!list) throw new Error('Learner list is missing.');
    expect(list.tagName).toBe('OL');
    expect(Array.from(list.querySelectorAll('li')).map((item) => item.textContent)).toEqual([
      'Mina proposes an activity.',
      'Jonas responds positively but asks about the time.',
      'Mina offers an alternative.',
      'They state the final plan clearly.',
    ]);
  });
});
