import { describe, expect, it } from 'vitest';

import { parseRoute } from './router';

describe('parseRoute', () => {
  it.each([
    ['/', { page: 'curriculum' }],
    [
      '/lessons/present_tense',
      {
        page: 'lesson',
        lessonId: 'present_tense',
        ui: { mode: 'review', stage: 'lesson', view: 'author' },
      },
    ],
    ['/settings', { page: 'settings' }],
    ['/settings', { page: 'settings' }],
    ['/lessons/Bad-Slug', { page: 'not-found' }],
    ['/lessons/..%2Fetc', { page: 'not-found' }],
    ['/lessons/%', { page: 'not-found' }],
    ['/elsewhere', { page: 'not-found' }],
  ])('test_parse_route_given_%s_expect_%o', (path, route) => {
    expect(parseRoute(path)).toEqual(route);
  });

  it('test_parse_route_given_valid_lesson_search_parameters_expect_validated_state', () => {
    expect(parseRoute('/lessons/present_tense', '?mode=read&stage=plan&view=source')).toEqual({
      page: 'lesson',
      lessonId: 'present_tense',
      ui: { mode: 'read', stage: 'plan', view: 'source' },
    });
  });

  it('test_parse_route_given_invalid_lesson_search_parameters_expect_default_state', () => {
    expect(parseRoute('/lessons/present_tense', '?mode=unsupported&stage=other&view=other')).toEqual({
      page: 'lesson',
      lessonId: 'present_tense',
      ui: { mode: 'review', stage: 'lesson', view: 'author' },
    });
  });
});
