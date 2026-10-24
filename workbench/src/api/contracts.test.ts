/** Entry point: test_frontend_fixtures_given_generated_wire_schema_expect_all_payloads_validate. */

import Ajv2020, { type AnySchema } from 'ajv/dist/2020.js';
import { describe, expect, it } from 'vitest';

import { curriculumPayload, lessonPayload, workbenchSettingsPayload } from '@/test/fixtures';
import schemaJson from '../../schema/workbench-api.schema.json';

const K_TEST_SCHEMA = schemaJson as unknown as AnySchema;

describe('workbench API schema', () => {
  it('test_frontend_fixtures_given_generated_wire_schema_expect_all_payloads_validate', () => {
    const ajv = new Ajv2020({ allErrors: true, strict: false });
    const validate = ajv.compile(K_TEST_SCHEMA);
    const payloads = {
      requests: {},
      responses: {
        curriculum: curriculumPayload(),
        lesson: lessonPayload(),
        settings: workbenchSettingsPayload(),
      },
      components: {},
    };

    expect(validate(payloads), ajv.errorsText(validate.errors)).toBe(true);
  });
});
