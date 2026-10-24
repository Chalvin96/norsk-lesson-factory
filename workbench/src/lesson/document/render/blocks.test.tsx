import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { BlockView } from './blocks';
import { ExercisePayload } from './exercise';

describe('BlockView', () => {
  it('test_example_validity_given_authored_roles_expect_incorrect_and_correct_markers', () => {
    render(
      <BlockView
        block={{
          kind: 'examples',
          items: [
            {
              teaching_role: 'incorrect',
              no: [{ kind: 'text', value: 'Gi meg en kaffe.' }],
              en: [{ kind: 'text', value: 'Give me a coffee.' }],
            },
            {
              teaching_role: 'model',
              no: [{ kind: 'text', value: 'Kan jeg få en kaffe?' }],
              en: [{ kind: 'text', value: 'Can I have a coffee?' }],
            },
          ],
        }}
      />,
    );

    expect(screen.getByLabelText('Incorrect example')).toHaveTextContent('✗ Incorrect');
    expect(screen.getByLabelText('Correct example')).toHaveTextContent('✓');
  });

  it('test_block_view_given_each_block_kind_expect_text_with_language_scoping', () => {
    const { container } = render(
      <>
        <BlockView
          block={{
            kind: 'reading',
            speaker_name: 'Mina',
            spans: [{ kind: 'text', value: 'Hei!' }],
            translation: 'Hi!',
          }}
        />
        <BlockView
          block={{
            kind: 'examples',
            items: [{ no: [{ kind: 'text', value: 'Jeg jobber.' }], en: [{ kind: 'text', value: 'I work.' }] }],
          }}
        />
        <BlockView block={{ kind: 'word_list', items: [{ term: 'å jobbe', form: 'jobber' }] }} />
        <BlockView
          block={{
            kind: 'table',
            col_langs: ['nb', 'en'],
            headers: [[{ kind: 'text', value: 'Norsk' }], [{ kind: 'text', value: 'English' }]],
            rows: [[[{ kind: 'text', value: 'hus' }], [{ kind: 'text', value: 'house' }]]],
          }}
        />
        <BlockView
          block={{
            kind: 'callout',
            level: 'warning',
            blocks: [{ kind: 'paragraph', spans: [{ kind: 'strong', value: 'Careful' }] }],
          }}
        />
      </>,
    );

    expect(screen.getByText('Hei!')).toHaveAttribute('lang', 'nb');
    expect(screen.getByText('Jeg jobber.')).toHaveAttribute('lang', 'nb');
    expect(screen.getByText('å jobbe')).toHaveAttribute('lang', 'nb');
    expect(screen.getByText('hus').closest('td')).toHaveAttribute('lang', 'nb');
    expect(screen.getByText('Careful').tagName).toBe('STRONG');
    expect(container.querySelector('script')).toBeNull();
  });

  it('test_block_view_given_unknown_block_kind_expect_unsupported_marker', () => {
    render(<BlockView block={{ kind: 'mystery' }} />);

    expect(screen.getByText('Unsupported block kind: mystery')).toBeInTheDocument();
  });
});

describe('ExercisePayload', () => {
  it('test_exercise_payload_given_choose_expect_keyed_answer_marked', () => {
    render(
      <ExercisePayload
        operation="choose"
        data={{
          answer_id: 'b',
          options: [
            { option_id: 'a', text: 'jobbe' },
            { option_id: 'b', text: 'jobber', why: 'Present tense.' },
          ],
        }}
      />,
    );

    expect(screen.getByText(/jobber · Answer/).closest('li')).toHaveAttribute('data-answer', 'true');
    expect(screen.getByText('Why: Present tense.')).toBeInTheDocument();
  });

  it('test_exercise_payload_given_recall_fill_expect_each_blank_with_keyed_option', () => {
    render(
      <ExercisePayload
        operation="recall_fill"
        data={{
          segments: [
            { kind: 'span', spans: [{ kind: 'text', value: 'Jeg ' }] },
            { kind: 'blank', blank_id: 'verb', options: ['jobbe', 'jobber'], answer_index: 1 },
          ],
        }}
      />,
    );

    expect(screen.getByText('verb = jobber')).toBeInTheDocument();
    expect(screen.getByText('Options: jobbe · jobber')).toBeInTheDocument();
  });

  it('test_exercise_payload_given_build_expect_answer_and_token_bank_with_fixed_marker', () => {
    render(
      <ExercisePayload
        operation="build"
        data={{
          answer_order: ['t2', 't1'],
          tokens: [
            { token_id: 't1', text: 'hjemme', fixed: true },
            { token_id: 't2', text: 'jobber' },
          ],
        }}
      />,
    );

    expect(screen.getByText('jobber hjemme')).toBeInTheDocument();
    expect(screen.getByText('Authored token bank')).toBeInTheDocument();
    expect(screen.getByText('hjemme [fixed] jobber')).toBeInTheDocument();
  });

  it('test_exercise_payload_given_match_pairs_expect_pairs_and_all_authored_sides_in_order', () => {
    render(
      <ExercisePayload
        operation="match_pairs"
        data={{
          left: [
            { left_id: 'l1', text: 'hund' },
            { left_id: 'l2', text: 'katt' },
          ],
          right: [
            { right_id: 'r1', text: 'cat' },
            { right_id: 'r2', text: 'dog' },
          ],
          pairs: [
            { left_id: 'l1', right_id: 'r2' },
            { left_id: 'l2', right_id: 'r1' },
          ],
        }}
      />,
    );

    expect(screen.getByText('hund → dog')).toBeInTheDocument();
    expect(screen.getByText('All authored sides')).toBeInTheDocument();
    const sides = screen.getByText('All authored sides').closest('details');
    expect(Array.from(sides?.querySelectorAll('li') ?? []).map((item) => item.textContent)).toEqual([
      'hund',
      'katt',
      'cat',
      'dog',
    ]);
  });
});
