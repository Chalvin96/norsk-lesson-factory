/** Entry point: `buildLessonIndexes`, called by the lesson workspace to index row data once. */

import type {
  ExerciseAlignmentState,
  FindingData,
  ProposalData,
  SourceBlock,
  WorkbenchAudioClip,
} from '@/api/contracts';

import { buildTargetKey } from './rows';

export interface LessonRowIndexes {
  findingsByTarget: Map<string, FindingData[]>;
  proposalsByTarget: Map<string, ProposalData[]>;
  audioByTarget: Map<string, WorkbenchAudioClip[]>;
  stalenessByExercise: Map<string, ExerciseAlignmentState>;
  blocksByTarget: Map<string, SourceBlock>;
  flagByTarget: Map<string, 'error' | 'warning'>;
  pendingProposalByTarget: Set<string>;
  openAiFindingsByCodeAndExcerpt: Map<FindingData['code'], Map<FindingData['excerpt'], FindingData[]>>;
}

export function buildLessonIndexes({
  findings,
  proposals,
  audioClips,
  exerciseStaleness,
  blocks,
}: {
  findings: FindingData[];
  proposals: ProposalData[];
  audioClips: WorkbenchAudioClip[];
  exerciseStaleness: ExerciseAlignmentState[];
  blocks: SourceBlock[];
}): LessonRowIndexes {
  const findingsByTarget = new Map<string, FindingData[]>();
  const proposalsByTarget = new Map<string, ProposalData[]>();
  const audioByTarget = new Map<string, WorkbenchAudioClip[]>();
  const stalenessByExercise = new Map<string, ExerciseAlignmentState>();
  const blocksByTarget = new Map<string, SourceBlock>();
  const flagByTarget = new Map<string, 'error' | 'warning'>();
  const pendingProposalByTarget = new Set<string>();
  const openAiFindingsByCodeAndExcerpt = new Map<FindingData['code'], Map<FindingData['excerpt'], FindingData[]>>();

  for (const finding of findings) {
    const targetKey = buildTargetKey(finding.target);
    append(findingsByTarget, targetKey, finding);
    if (finding.status === 'open') {
      if (finding.severity === 'error') flagByTarget.set(targetKey, 'error');
      else if (!flagByTarget.has(targetKey)) flagByTarget.set(targetKey, 'warning');
    }
    if (finding.origin !== 'ai' || finding.status !== 'open') continue;
    let excerpts = openAiFindingsByCodeAndExcerpt.get(finding.code);
    if (!excerpts) {
      excerpts = new Map();
      openAiFindingsByCodeAndExcerpt.set(finding.code, excerpts);
    }
    append(excerpts, finding.excerpt, finding);
  }

  for (const proposal of proposals) {
    const targetKey = buildTargetKey(proposal.target);
    append(proposalsByTarget, targetKey, proposal);
    if (proposal.status === 'pending' || proposal.status === 'conflicted') pendingProposalByTarget.add(targetKey);
  }
  for (const clip of audioClips) append(audioByTarget, clip.rowKey, clip);
  for (const item of exerciseStaleness) {
    if (item.exerciseKey) stalenessByExercise.set(item.exerciseKey, item);
  }
  for (const block of blocks) blocksByTarget.set(block.key, block);

  return {
    findingsByTarget,
    proposalsByTarget,
    audioByTarget,
    stalenessByExercise,
    blocksByTarget,
    flagByTarget,
    pendingProposalByTarget,
    openAiFindingsByCodeAndExcerpt,
  };
}

function append<Key, Value>(map: Map<Key, Value[]>, key: Key, value: Value): void {
  const current = map.get(key);
  if (current) current.push(value);
  else map.set(key, [value]);
}
