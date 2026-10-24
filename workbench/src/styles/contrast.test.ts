import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const tokens = readFileSync(resolve(import.meta.dirname, 'tokens.css'), 'utf8');
const overrides = readFileSync(resolve(import.meta.dirname, 'workbench-overrides.css'), 'utf8');

function parseProperties(css: string, selector = ':root') {
  return Object.fromEntries(
    [...css.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^}]+)\}/g)]
      .filter((block) =>
        block[1]
          .split(',')
          .map((item) => item.trim())
          .includes(selector),
      )
      .flatMap((block) =>
        [...block[2].matchAll(/(--[\w-]+):\s*([^;]+);/g)].map((match) => [match[1], match[2].trim()]),
      ),
  );
}

const light = parseProperties(`${tokens}\n${overrides}`);
const dark = {
  ...light,
  ...parseProperties(`${tokens}\n${overrides}`, '.dark'),
  ...parseProperties(overrides, '.dark .dialog-content'),
};
function resolveColor(theme: Record<string, string>, name: string, backdrop = [255, 255, 255]): number[] {
  const value = theme[name];
  if (!value) throw new Error(`Missing CSS property ${name}`);
  const alias = /^var\((--[\w-]+)\)$/.exec(value);
  if (alias) return resolveColor(theme, alias[1], backdrop);
  if (value.startsWith('#')) return [1, 3, 5].map((offset) => Number.parseInt(value.slice(offset, offset + 2), 16));
  const oklch = /^oklch\(([\d.]+)% ([\d.]+) ([\d.]+)\)$/.exec(value);
  if (oklch) {
    const l = Number(oklch[1]) / 100;
    const a = Number(oklch[2]) * Math.cos((Number(oklch[3]) * Math.PI) / 180);
    const b = Number(oklch[2]) * Math.sin((Number(oklch[3]) * Math.PI) / 180);
    const ll = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
    const m = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
    const ss = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;
    return [
      4.0767416621 * ll - 3.3077115913 * m + 0.2309699292 * ss,
      -1.2684380046 * ll + 2.6097574011 * m - 0.3413193965 * ss,
      -0.0041960863 * ll - 0.7034186147 * m + 1.707614701 * ss,
    ].map((channel) => 255 * (channel <= 0.0031308 ? 12.92 * channel : 1.055 * channel ** (1 / 2.4) - 0.055));
  }
  const rgba = /^rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)$/.exec(value);
  if (!rgba) throw new Error(`Unsupported CSS color ${value}`);
  const alpha = Number(rgba[4]);
  return rgba.slice(1, 4).map((channel, index) => Number(channel) * alpha + backdrop[index] * (1 - alpha));
}

function computeLuminance(rgb: number[]) {
  const linear = rgb.map((channel) => {
    const value = channel / 255;
    return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
  });
  return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722;
}

function computeContrast(theme: Record<string, string>, foreground: string, background: string) {
  const values = [
    computeLuminance(resolveColor(theme, foreground, resolveColor(theme, background))),
    computeLuminance(resolveColor(theme, background)),
  ].sort((a, b) => b - a);
  return (values[0] + 0.05) / (values[1] + 0.05);
}

describe.each([
  ['light', light],
  ['dark', dark],
] as const)('Workbench %s CSS contrast', (_mode, theme) => {
  it('test_theme_given_surrounding_screens_expect_original_color_tokens', () => {
    expect(theme['--primary']).toBe(_mode === 'dark' ? 'var(--primary-60)' : 'var(--primary-90)');
    const original = { ...parseProperties(tokens), ...(_mode === 'dark' ? parseProperties(tokens, '.dark') : {}) };
    const surroundingTheme = {
      ...light,
      ...(_mode === 'dark' ? parseProperties(`${tokens}\n${overrides}`, '.dark') : {}),
    };
    for (const name of [
      '--input',
      '--ring',
      '--surface-mat',
      '--progress-primary-complete',
      '--progress-primary-current',
      '--progress-primary-locked',
      '--progress-secondary-complete',
      '--progress-secondary-current',
      '--progress-secondary-locked',
    ])
      expect(surroundingTheme[name]).toBe(original[name]);
  });
  it('test_dialog_given_theme_expect_distinct_surface_and_visible_hover', () => {
    expect(resolveColor(theme, '--popover')).not.toEqual(resolveColor(theme, '--background'));
    const base = computeLuminance(resolveColor(theme, '--button-primary'));
    const hover = computeLuminance(resolveColor(theme, '--button-primary-hover'));
    if (_mode === 'dark') expect(hover).toBeGreaterThanOrEqual(base);
  });
  it.each([
    ['field_border', '--border-strong', '--popover', 3],
    ['focused_border', '--ring', '--popover', 3],
    ['invalid_border', '--destructive', '--popover', 3],
    ['error_message', '--destructive', '--popover', 4.5],
    ['helper_text', '--muted-foreground', '--popover', 4.5],
    ['label', '--foreground', '--popover', 4.5],
    ['primary_label', '--primary-foreground', '--button-primary', 4.5],
    ['primary_hover_label', '--primary-foreground', '--button-primary-hover', 4.5],
    ['disabled_primary_label', '--muted-foreground', '--control-disabled', 4.5],
    ['helper_on_background', '--muted-foreground', '--background', 4.5],
    ['disabled_fill_border', '--border', '--control-disabled', 1.1],
    ['helper_on_mat', '--muted-foreground', '--surface-mat', 4.5],
    ['outline_label', '--foreground', '--popover', 4.5],
  ])('test_contrast_given_%s_expect_minimum_ratio', (_pair, foreground, background, minimum) => {
    expect(computeContrast(theme, String(foreground), String(background))).toBeGreaterThanOrEqual(Number(minimum));
  });
});
