/** Entry point: fixture helpers build complete generated workbench wire values. */

import type { WorkbenchWire } from '@/api/contracts';

type ApiCatalog = WorkbenchWire.WorkbenchApiSchemaCatalog;
type LessonState = NonNullable<ApiCatalog['responses']['lesson']>['state'];
type WireFinding = LessonState['findings'][number];
type WireProposal = LessonState['proposals'][number];
type WireChatTurn = LessonState['chat'][number];
type WireContextChip = WireChatTurn['chips'][number];
type ChatTurnFixtureInput = Pick<WireChatTurn, 'role' | 'text'> & {
  chips?: Array<{
    kind: WireContextChip['kind'];
    label: WireContextChip['label'];
    detail: WireContextChip['detail'];
    key?: WireContextChip['key'];
    referenceId?: WireContextChip['referenceId'];
  }>;
  toolNote?: WireChatTurn['toolNote'];
  proposedEdit?: WireChatTurn['proposedEdit'];
  proposalId?: WireChatTurn['proposalId'];
  turnId?: WireChatTurn['turnId'];
  codexTurnId?: WireChatTurn['codexTurnId'];
  status?: WireChatTurn['status'];
  partial?: WireChatTurn['partial'];
  error?: WireChatTurn['error'];
  kind?: WireChatTurn['kind'];
  origin?: WireChatTurn['origin'];
  mode?: WireChatTurn['mode'];
  blockId?: WireChatTurn['blockId'];
  blockHash?: WireChatTurn['blockHash'];
};

export function findingFixture(
  value: Pick<
    WireFinding,
    'findingId' | 'target' | 'origin' | 'severity' | 'status' | 'code' | 'excerpt' | 'explanation'
  > &
    Partial<WireFinding>,
): WireFinding {
  return {
    resolutionNote: '',
    resolutionReason: null,
    resolutionComment: null,
    previouslyResolved: false,
    level: 'minor',
    advisory: false,
    reviewer: 'primary',
    label: 'Review finding',
    message: '',
    quoteEnglish: '',
    suggestedChange: '',
    details: '',
    ...value,
  };
}

export function proposalFixture(
  value: Pick<WireProposal, 'proposalId' | 'before' | 'after' | 'note' | 'allowedDecisions'> & Partial<WireProposal>,
): WireProposal {
  return {
    target: { elementId: 'fixture', blockIndex: null },
    origin: 'chat',
    key: '',
    beforeRendered: [],
    afterRendered: [],
    status: 'pending',
    check: { status: 'passed', errors: [] },
    changeSummary: '',
    translations: [],
    answerImpact: 'none',
    uncertainty: null,
    changedSections: [],
    ...value,
  };
}

export function chatTurnFixture(value: ChatTurnFixtureInput): WireChatTurn {
  return {
    toolNote: null,
    proposedEdit: null,
    proposalId: null,
    turnId: null,
    codexTurnId: null,
    status: null,
    partial: '',
    error: null,
    kind: 'lesson',
    origin: 'chat',
    mode: 'chat',
    blockId: null,
    blockHash: null,
    ...value,
    chips: (value.chips ?? []).map(({ key, referenceId, ...chip }) => ({
      ...chip,
      key: key ?? null,
      referenceId: referenceId ?? null,
    })),
  };
}
