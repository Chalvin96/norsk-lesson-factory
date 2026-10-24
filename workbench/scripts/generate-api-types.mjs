/** Entry point: generateWorkbenchTypes writes or checks the generated API types. */

import { readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

import { compileFromFile } from 'json-schema-to-typescript';

const K_GENERATOR_SCHEMA_PATH = fileURLToPath(new URL('../schema/workbench-api.schema.json', import.meta.url));
const K_GENERATOR_TYPES_PATH = fileURLToPath(new URL('../src/api/types.generated.ts', import.meta.url));

async function generateWorkbenchTypes({ check = false } = {}) {
  const output = await compileFromFile(K_GENERATOR_SCHEMA_PATH, 'WorkbenchApiSchemaCatalog', {
    bannerComment: '/* Generated from schema/workbench-api.schema.json. Do not edit directly. */',
    style: {
      singleQuote: true,
      semi: true,
    },
  });

  if (check) {
    const current = await readFile(K_GENERATOR_TYPES_PATH, 'utf8').catch(() => '');
    if (current !== output) {
      throw new Error(`Generated workbench types are stale: ${K_GENERATOR_TYPES_PATH}`);
    }
    return;
  }

  await writeFile(K_GENERATOR_TYPES_PATH, output, 'utf8');
}

await generateWorkbenchTypes({ check: process.argv.includes('--check') });
