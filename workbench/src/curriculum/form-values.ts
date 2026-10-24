/** Entry points: `deriveSlug`, `parseLessonIds`, `nonEmptyLessonIds`, and `multipleLessonIds`, called by curriculum controls. */

export function deriveSlug(value: string): string {
  return value
    .toLowerCase()
    .replace(/æ/g, 'ae')
    .replace(/ø/g, 'o')
    .replace(/å/g, 'aa')
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '');
}

export function parseLessonIds(value: string): string[] {
  return value
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

export function nonEmptyLessonIds(lessonIds: string[]): [string, ...string[]] | null {
  const [first, ...remaining] = lessonIds;
  return first === undefined ? null : [first, ...remaining];
}

export function multipleLessonIds(lessonIds: string[]): [string, string, ...string[]] | null {
  const [first, second, ...remaining] = lessonIds;
  return first === undefined || second === undefined ? null : [first, second, ...remaining];
}
