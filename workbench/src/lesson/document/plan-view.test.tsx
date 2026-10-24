import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { PlanView } from './plan-view';

const generatedFrontmatter = {
  package_version: '0.1-catalog-batch',
  lesson_id: 'demo',
  catalog_id: 'demo',
  kind: 'communicative',
  title: 'Be om hjelp — ask for help',
  cefr_level: 'A2',
  cefr_target_basis: 'planner_default',
  approved: true,
  learner_outcome: 'Ask for help and react to an offer of help.',
  family_id: 'transactional_interaction',
  cefr_tags: ['A2'],
  teaching_points: [
    { id: 'tp-1', source_owner: 'demo', statement: 'V2 word order after help requests' },
    { id: 'tp-2', source_owner: 'demo', statement: 'Polite requests with kan du' },
  ],
  terminology_ids: ['aa_be_om', 'unnskyld'],
  prerequisites: ['greetings'],
  helpful_prerequisites: ['question_word_order'],
  objectives: [{ id: 'obj-1', statement: 'Ask for help.' }],
  coverage: [{ id: 'cov-1', objective: 'obj-1', claim: 'Ask for help.', scope: 'required' }],
  success_criteria: ['Ask for help.'],
  exclusions: ['open speaking assessment'],
};

function renderGeneratedPlan() {
  const source = '---\ntitle: Be om hjelp\n---\n# Plan';
  render(
    <PlanView source={source} preview={{ frontmatter: generatedFrontmatter, body: '# Plan', parseError: null }} />,
  );
}

describe('Plan view', () => {
  it('test_plan_view_given_parsed_constraints_expect_values_and_raw_source_disclosure', () => {
    const source =
      '---\nprerequisites:\n- invite_someone_and_respond\nexclusions:\n- open speaking assessment\n---\n# Plan';
    render(
      <PlanView
        source={source}
        preview={{
          frontmatter: {
            prerequisites: ['invite_someone_and_respond'],
            exclusions: ['open speaking assessment', 'learner-specific scoring'],
          },
          body: '# Plan',
          parseError: null,
        }}
      />,
    );

    expect(screen.getByText('invite_someone_and_respond')).toBeInTheDocument();
    expect(screen.getByText('Exclusions')).toBeVisible();
    expect(screen.getByText('open speaking assessment')).toBeVisible();
    const rawSource = screen.getByText('Raw plan source').closest('details');
    if (!rawSource) throw new Error('Raw plan source disclosure is missing.');
    expect(rawSource).not.toHaveAttribute('open');
    fireEvent.click(screen.getByText('Raw plan source'));
    expect(
      within(rawSource).getByText((_, element) => element?.tagName === 'PRE' && element.textContent === source),
    ).toBeInTheDocument();
  });

  it('test_plan_view_given_generated_plan_frontmatter_expect_author_fields_only_with_technical_disclosure', () => {
    renderGeneratedPlan();

    const authorList = screen.getByText('Title').closest('dl');
    if (!authorList) throw new Error('Author field list is missing.');
    expect(within(authorList).getByText('Title')).toBeVisible();
    expect(within(authorList).getByText('Be om hjelp — ask for help')).toBeVisible();
    expect(within(authorList).getByText('CEFR level')).toBeVisible();
    expect(within(authorList).getAllByText('A2')).toHaveLength(2);
    expect(within(authorList).getByText('Learner outcome')).toBeVisible();
    expect(within(authorList).getByText('Ask for help and react to an offer of help.')).toBeVisible();
    expect(within(authorList).getByText('Objectives')).toBeVisible();
    expect(within(authorList).getAllByText('Ask for help.')).toHaveLength(3);
    expect(within(authorList).getByText('Teaching points')).toBeVisible();
    expect(within(authorList).getByText('V2 word order after help requests')).toBeVisible();
    expect(within(authorList).getByText('Polite requests with kan du')).toBeVisible();
    expect(within(authorList).getByText('Terminology')).toBeVisible();
    expect(within(authorList).getByText('aa_be_om')).toBeVisible();
    expect(within(authorList).getByText('Prerequisites')).toBeVisible();
    expect(within(authorList).getByText('greetings')).toBeVisible();
    expect(within(authorList).getByText('Helpful prerequisites')).toBeVisible();
    expect(within(authorList).getByText('question_word_order')).toBeVisible();
    expect(within(authorList).queryByText('tp-1')).toBeNull();
    expect(within(authorList).queryByText('package version')).toBeNull();
    expect(within(authorList).queryByText('catalog id')).toBeNull();
    expect(within(authorList).queryByText('cefr target basis')).toBeNull();
    for (const label of ['Coverage', 'Success criteria', 'Exclusions', 'Kind', 'Family']) {
      expect(within(authorList).getByText(label)).toBeVisible();
    }
    expect(within(authorList).getByText('transactional_interaction')).toBeVisible();
    expect(within(authorList).getByText('communicative')).toBeVisible();
    expect(within(authorList).getByText('open speaking assessment')).toBeVisible();
    expect(screen.getByText('source owner')).not.toBeVisible();

    const technical = screen.getByText('Technical fields').closest('details');
    if (!technical) throw new Error('Technical fields disclosure is missing.');
    expect(technical).not.toHaveAttribute('open');
    expect(within(technical).getByText('package version')).not.toBeVisible();
    fireEvent.click(screen.getByText('Technical fields'));
    expect(within(technical).getByText('package version')).toBeVisible();
    expect(within(technical).getByText('0.1-catalog-batch')).toBeVisible();
    expect(within(technical).getByText('catalog id')).toBeVisible();
    expect(within(technical).getByText('cefr target basis')).toBeVisible();
    expect(within(technical).getByText('planner_default')).toBeVisible();
    expect(within(technical).queryByText('family id')).toBeNull();
    expect(within(technical).getByText('source owner')).toBeVisible();
  });

  it('test_plan_view_given_structured_coverage_expect_claim_scope_and_evidence_without_ids', () => {
    render(
      <PlanView
        source=""
        preview={{
          frontmatter: {
            coverage: [
              {
                id: 'cov-help',
                objective: 'obj-help',
                claim: 'Ask for help.',
                scope: 'required',
                evidence: ['explanation', 'example', 'controlled_practice'],
              },
              {
                id: 'cov-boundary',
                claim: 'Keep one learner outcome.',
                scope: 'guardrail',
                evidence: ['whole_lesson'],
              },
            ],
          },
          body: '',
          parseError: null,
        }}
      />,
    );

    const required = screen.getByText('Ask for help.').closest('li');
    const guardrail = screen.getByText('Keep one learner outcome.').closest('li');
    if (!required || !guardrail) throw new Error('Coverage items are missing.');
    expect(within(required).getByText('required')).toBeVisible();
    expect(within(required).getByRole('list', { name: 'Evidence kinds' })).toHaveTextContent(
      'explanationexamplecontrolled practice',
    );
    expect(within(guardrail).getByText('guardrail')).toBeVisible();
    expect(within(guardrail).getByRole('list', { name: 'Evidence kinds' })).toHaveTextContent('whole lesson');
    expect(screen.queryByText('cov-help')).toBeNull();
    expect(screen.queryByText('obj-help')).toBeNull();
    expect(screen.queryByText('cov-boundary')).toBeNull();
    expect(screen.queryByText('id')).toBeNull();
    expect(screen.queryByText('objective')).toBeNull();
  });

  it.each(['objectives', 'success_criteria', 'exclusions', 'teaching_points'])(
    'test_plan_view_given_structured_author_items_expect_human_text_without_ids',
    (field) => {
      render(
        <PlanView
          source=""
          preview={{
            frontmatter: {
              [field]: [
                { id: 'internal-statement', statement: 'Recognize a request.' },
                { id: 'internal-claim', claim: 'Use a polite form.' },
                { id: 'internal-text', text: '<script>untrusted</script>' },
                'Keep plain strings too.',
              ],
            },
            body: '',
            parseError: null,
          }}
        />,
      );

      for (const text of [
        'Recognize a request.',
        'Use a polite form.',
        '<script>untrusted</script>',
        'Keep plain strings too.',
      ]) {
        expect(screen.getByText(text)).toBeVisible();
      }
      expect(screen.queryByText(/^internal-/)).toBeNull();
      expect(screen.queryByText('id')).toBeNull();
      expect(document.querySelector('script')).toBeNull();
    },
  );

  it.each([
    { family_id: '' },
    { family_id: '  \t' },
    { family_id: null },
    {},
    {
      family_id: '',
      provisional_flags: ['Family assignment is optional. Choose one by learner outcome when a catalog family fits.'],
    },
  ])('test_plan_view_given_unassigned_family_expect_visible_unassigned_choice', (frontmatter) => {
    render(<PlanView source="" preview={{ frontmatter, body: '', parseError: null }} />);

    expect(screen.getByText('Family')).toBeVisible();
    expect(screen.getByText('Unassigned')).toBeVisible();
  });

  it.each([
    {
      field: 'objectives',
      value: [{ id: 'unexpected-id', rationale: 'Read this rationale.' }],
      expected: 'Read this rationale.',
    },
    { field: 'objectives', value: 42, expected: '42' },
    { field: 'objectives', value: 'A string instead of a list.', expected: 'A string instead of a list.' },
    { field: 'objectives', value: [[{ rationale: 'Nested rationale.' }]], expected: 'Nested rationale.' },
    {
      field: 'coverage',
      value: [{ claim: 'Check evidence.', evidence: ['example', { reason: 'Unexpected evidence.' }, 7] }],
      expected: 'Unexpected evidence.',
    },
    {
      field: 'coverage',
      value: [{ claim: 'Check evidence.', evidence: { reason: 'Object evidence.' } }],
      expected: 'Object evidence.',
    },
    { field: 'extra_context', value: { rationale: 'Extra rationale.' }, expected: 'Extra rationale.' },
    {
      field: 'extra_context',
      value: { claim: '', rationale: 'Unexpected extra rationale.' },
      expected: 'Unexpected extra rationale.',
    },
  ])('test_plan_view_given_unexpected_author_value_expect_readable_content', ({ field, value, expected }) => {
    render(<PlanView source="" preview={{ frontmatter: { [field]: value }, body: '', parseError: null }} />);

    expect(screen.getByText(expected)).toBeVisible();
  });

  it('test_plan_view_given_null_known_author_field_expect_visible_placeholder', () => {
    render(<PlanView source="" preview={{ frontmatter: { objectives: null }, body: '', parseError: null }} />);

    const label = screen.getByText('Objectives');
    expect(label.nextElementSibling).toHaveTextContent('—');
  });

  it.each([null, [], {}, [null, {}]])(
    'test_plan_view_given_empty_unknown_field_expect_technical_disclosure',
    (value) => {
      render(<PlanView source="" preview={{ frontmatter: { extra_context: value }, body: '', parseError: null }} />);

      const technical = screen.getByText('Technical fields').closest('details');
      if (!technical) throw new Error('Technical fields disclosure is missing.');
      expect(within(technical).getByText('extra context')).not.toBeVisible();
    },
  );

  it('test_plan_view_given_top_level_and_teaching_point_source_owner_expect_one_row_with_unique_owners', () => {
    render(
      <PlanView
        source=""
        preview={{
          frontmatter: {
            source_owner: 'top-owner',
            teaching_points: [
              { statement: 'First point.', source_owner: 'top-owner' },
              { statement: 'Second point.', source_owner: 'point-owner' },
            ],
          },
          body: '',
          parseError: null,
        }}
      />,
    );

    fireEvent.click(screen.getByText('Technical fields'));
    expect(screen.getAllByText('source owner')).toHaveLength(1);
    expect(screen.getAllByText('top-owner')).toHaveLength(1);
    expect(screen.getByText('point-owner')).toBeVisible();
  });

  it('test_plan_view_given_approval_flag_frontmatter_expect_flag_only_inside_technical_disclosure', () => {
    renderGeneratedPlan();

    const authorList = screen.getByText('Title').closest('dl');
    if (!authorList) throw new Error('Author field list is missing.');
    expect(within(authorList).queryByText('approved')).toBeNull();
    const technical = screen.getByText('Technical fields').closest('details');
    if (!technical) throw new Error('Technical fields disclosure is missing.');
    expect(within(technical).getByText('approved')).not.toBeVisible();
    fireEvent.click(screen.getByText('Technical fields'));
    expect(within(technical).getByText('approved')).toBeVisible();
    expect(within(technical).getByText('true')).toBeVisible();
  });

  it('test_plan_view_given_invalid_frontmatter_expect_parse_error_and_original_source_available', () => {
    const source = '---\nprerequisites: [broken\n---\n# Raw plan';
    render(<PlanView source={source} preview={{ frontmatter: {}, body: '', parseError: 'Invalid YAML' }} />);

    expect(screen.getByRole('status')).toHaveTextContent('Plan frontmatter could not be parsed: Invalid YAML');
    fireEvent.click(screen.getByText('Raw plan source'));
    expect(
      screen.getByText((_, element) => element?.tagName === 'PRE' && element.textContent === source),
    ).toBeInTheDocument();
  });
});
