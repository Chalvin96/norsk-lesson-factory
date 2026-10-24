import type { BlockTarget, PacketElement } from '@/api/contracts';
import type { Block } from '@/lesson/document/render/blocks';

export type RowKind = 'heading' | 'block' | 'exercise';

export interface DocRow {
  rowId: string;
  targetKey: string;
  kind: RowKind;
  blockKind: string;
  sectionLabel: string;
  element: PacketElement;
  block?: Block;
}

export function buildTargetKey(target: BlockTarget): string {
  return target.blockIndex == null ? target.elementId : `${target.elementId}#${target.blockIndex}`;
}

export function buildRows(elements: PacketElement[]): DocRow[] {
  const rows: DocRow[] = [];
  for (const element of elements) {
    const elementId = String(element.id ?? '');
    const anchor = String(element.anchor_id ?? elementId);
    if (element.kind === 'section') {
      const label = String(element.title ?? '');
      rows.push({
        rowId: anchor,
        targetKey: elementId,
        kind: 'heading',
        blockKind: 'heading',
        sectionLabel: label,
        element,
      });
      const blocks = Array.isArray(element.blocks) ? (element.blocks as Block[]) : [];
      blocks.forEach((block, index) => {
        rows.push({
          rowId: `${anchor}-b${index}`,
          targetKey: `${elementId}#${index}`,
          kind: 'block',
          blockKind: String(block.kind ?? 'block'),
          sectionLabel: label,
          element,
          block,
        });
      });
    } else {
      rows.push({
        rowId: anchor,
        targetKey: elementId,
        kind: 'exercise',
        blockKind: 'exercise',
        sectionLabel: `Exercise · ${String(element.operation ?? 'exercise')}`,
        element,
      });
    }
  }
  return rows;
}
