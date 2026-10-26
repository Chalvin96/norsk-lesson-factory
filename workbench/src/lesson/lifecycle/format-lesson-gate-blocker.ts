/** Entry point: `formatLessonGateBlocker` presents one typed lesson gate reason. */

import type { LessonGateBlocker } from '@/api/contracts';

export function formatLessonGateBlocker(blocker: LessonGateBlocker): string {
  switch (blocker.kind) {
    case 'check_errors':
      return blocker.stage ? `${stageLabel(blocker.stage)}: Blocking check errors` : 'Blocking check errors';
    case 'open_proposal':
      return 'Open proposal';
    case 'audio':
      if (blocker.missing || blocker.stale) {
        const missing = blocker.missing ? `${blocker.missing} missing` : '';
        const stale = blocker.stale ? `${blocker.stale} stale` : '';
        return `Audio: ${[missing, stale].filter(Boolean).join(', ')} clips`;
      }
      return 'Audio checks need review';
    case 'canonical_changed':
      return 'Canonical source changed since this draft started';
    case 'no_draft':
      return 'Draft required';
    case 'unsaved_changes':
      return 'Save changes first';
    default: {
      const exhaustive: never = blocker;
      return exhaustive;
    }
  }
}

function stageLabel(stage: 'plan' | 'lesson' | 'exercises'): string {
  return stage === 'plan' ? 'Plan' : stage === 'lesson' ? 'Lesson' : 'Exercises';
}
