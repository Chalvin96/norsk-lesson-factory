/** Not a check itself — aliases for generated workbench transport contracts. */

import type * as Wire from './types.generated';

export type * as WorkbenchWire from './types.generated';

type ApiCatalog = Wire.WorkbenchApiSchemaCatalog;
type CurriculumResponse = NonNullable<ApiCatalog['responses']['curriculum']>;
type CurriculumChecksResponse = NonNullable<ApiCatalog['responses']['curriculumChecks']>;
type LessonResponse = NonNullable<ApiCatalog['responses']['lesson']>;
type SettingsResponse = NonNullable<ApiCatalog['responses']['settings']>;
type LessonStateWire = LessonResponse['state'];

export type CurriculumPayload = CurriculumResponse;
export type CurriculumChecks = CurriculumChecksResponse;
export type CurriculumLesson = CurriculumResponse['lessons'][number];
export type CurriculumEntry = CurriculumResponse['entries'][string];
export type CurriculumFinding = CurriculumResponse['findings'][number];
export type StatusChipData = CurriculumEntry['statusChips'][number];
export type ChipTone = StatusChipData['tone'];

export type LessonPayload = LessonResponse;
export type LessonState = LessonStateWire;
export type WorkbenchFinding = LessonStateWire['findings'][number];
export type FindingData = WorkbenchFinding;
export type FindingOrigin = WorkbenchFinding['origin'];
export type FindingSeverity = WorkbenchFinding['severity'];
export type FindingStatus = WorkbenchFinding['status'];
export type ProposalData = LessonStateWire['proposals'][number];
export type WorkbenchProposal = ProposalData;
export type ProposalText = NonNullable<ProposalData['beforeRendered']>[number];
export type ProposalOrigin = ProposalData['origin'];
export type ProposalDecision = ProposalData['allowedDecisions'][number];
export type ProposalStatus = ProposalData['status'];
export type ProposalCheck = NonNullable<ProposalData['check']>;
export type ExerciseSectionChange = NonNullable<ProposalData['changedSections']>[number];
export type ExerciseAlignmentState = LessonStateWire['exerciseStaleness'][number];
export type BlockExplanation = LessonStateWire['explanations'][string];
export type BlockTarget = WorkbenchFinding['target'];
export type StageApproval = LessonStateWire['approvalStages'][number];
export type LessonGateBlocker = LessonStateWire['approveBlockers'][number];
export type LessonGateStage = NonNullable<ApiCatalog['components']['LessonGateStage']>;

export type CurriculumEditPreview = NonNullable<ApiCatalog['responses']['curriculumPreview']>;
export type CurriculumPreviewEntry = CurriculumEditPreview['entries'][number];
export type CurriculumSaveResult = NonNullable<ApiCatalog['responses']['curriculumSave']>;
export type LessonActionResult = NonNullable<ApiCatalog['responses']['lessonAction']>;
export type WriteResult = NonNullable<ApiCatalog['responses']['write']>;
export type SaveResult = NonNullable<ApiCatalog['responses']['save']>;
export type CheckResult = WriteResult['check'];
export type SourceBlock = LessonResponse['blocks'][number];
export type DraftStatus = LessonResponse['draft'];
export type SourceFinding = LessonResponse['sourceFindings'][number];
export type PlanSourceProjection = LessonResponse['planPreview'];

export type WorkbenchAudioClipStatus = LessonResponse['audio']['clips'][number];
export type WorkbenchAudioClip = WorkbenchAudioClipStatus;
export type WorkbenchAudioVoice = LessonResponse['audio']['voices'][number];
export type WorkbenchAudioStatus = LessonResponse['audio'];
export type WorkbenchAudioPlan = NonNullable<ApiCatalog['responses']['audioPlan']>['plan'];
export type WorkbenchAudioPlanClip = WorkbenchAudioPlan['clips'][number];

export type WorkbenchSettingsResponse = SettingsResponse;
export type WorkbenchSettings = SettingsResponse['settings'];
export type WorkbenchRole = keyof WorkbenchSettings['roles'];
export type WorkbenchEffort = WorkbenchSettings['roles'][WorkbenchRole]['effort'];
export type ReviewScope = WorkbenchSettings['defaults']['review_scope'];
export type RegenerationRun = NonNullable<ApiCatalog['responses']['run']>;

export type ContextChipKind = NonNullable<ApiCatalog['components']['ContextChipKind']>;

export type NewLessonRequest = NonNullable<ApiCatalog['requests']['curriculum.lesson.create']>;
export type BlockWrite = NonNullable<ApiCatalog['requests']['lesson.block.put']>;
export type ApproveRequest = NonNullable<ApiCatalog['requests']['lesson.approve']>;

/** Lesson packet elements retain their authored snake_case keys for rendering. */
export type PacketElement = Record<string, unknown> & {
  kind?: string;
  id?: string;
  anchor_id?: string;
};
