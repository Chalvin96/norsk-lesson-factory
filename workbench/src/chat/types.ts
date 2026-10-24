/** Not a check itself — chat turns and composer context owned by the chat feature. */

import type { ContextChipKind, WorkbenchWire } from '@/api/contracts';

type ApiCatalog = WorkbenchWire.WorkbenchApiSchemaCatalog;
type LessonStateWire = NonNullable<ApiCatalog['responses']['lesson']>['state'];
type StoredChatTurnWire = LessonStateWire['chat'][number];

/** Composer chip shape before the server fills optional reference identifiers. */
export interface ContextChipData {
  kind: ContextChipKind;
  label: string;
  detail: string;
  key?: string | null;
  referenceId?: string | null;
}

/** Persisted wire turn with optional fields while a new local turn is being composed. */
export interface ChatTurnData {
  role: StoredChatTurnWire['role'];
  text: StoredChatTurnWire['text'];
  toolNote: StoredChatTurnWire['toolNote'];
  chips: ContextChipData[];
  proposedEdit?: StoredChatTurnWire['proposedEdit'];
  proposalId?: StoredChatTurnWire['proposalId'];
  turnId?: StoredChatTurnWire['turnId'];
  codexTurnId?: StoredChatTurnWire['codexTurnId'];
  status?: StoredChatTurnWire['status'];
  partial?: StoredChatTurnWire['partial'];
  error?: StoredChatTurnWire['error'];
  kind?: StoredChatTurnWire['kind'];
  origin?: StoredChatTurnWire['origin'];
  mode?: StoredChatTurnWire['mode'];
  blockId?: StoredChatTurnWire['blockId'];
  blockHash?: StoredChatTurnWire['blockHash'];
}

export type ActiveTurnData = Exclude<LessonStateWire['activeTurn'], null>;
