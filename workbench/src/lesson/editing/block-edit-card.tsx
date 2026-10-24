import * as React from 'react';

import { toast } from 'sonner';
import type { SourceBlock } from '@/api/contracts';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Textarea } from '@/components/ui/textarea';
import { useWriteBlock } from '@/lesson/api';

import type { DocRow } from '../document/rows';

export interface BlockEditCardProps {
  row: DocRow;
  lessonId: string;
  blockSource: SourceBlock | undefined;
  onDone: (changed: boolean) => void;
  onCancel: () => void;
  onDirtyChange: (dirty: boolean) => void;
}

export function BlockEditCard({ row, lessonId, blockSource, onDone, onCancel, onDirtyChange }: BlockEditCardProps) {
  const mutation = useWriteBlock(lessonId);
  const fields = React.useMemo(
    () =>
      blockSource
        ? Object.entries(blockSource.fields).map(([name, value]) => ({
            name,
            value,
            lang: name === 'body' || name.startsWith('no') ? 'nb' : 'en',
            multiline: name === 'text' || name === 'statement',
          }))
        : [],
    [blockSource],
  );
  const [values, setValues] = React.useState(() => fields.map((field) => field.value));
  const [raw, setRaw] = React.useState(blockSource?.source ?? '');
  const [tab, setTab] = React.useState(fields.length ? 'form' : 'source');
  const changed =
    tab === 'source'
      ? raw !== (blockSource?.source ?? '')
      : values.some((value, index) => value !== fields[index]?.value);
  React.useEffect(() => onDirtyChange(changed), [changed, onDirtyChange]);
  const done = () => {
    if (!changed) {
      onDone(false);
      return;
    }
    const body =
      tab === 'source'
        ? { source: raw }
        : {
            fields: Object.fromEntries(fields.map((field, index) => [field.name, values[index] ?? ''])),
          };
    mutation.mutate(
      { key: row.targetKey, body },
      {
        onSuccess: () => onDone(true),
        onError: (error) => toast.error(error.message),
      },
    );
  };

  return (
    <fieldset
      aria-label="Edit block"
      className="my-2 rounded-xl border border-primary-50 bg-white p-3 shadow-[0_0_0_3px_var(--primary-10)]"
      onKeyDown={(event) => {
        if (event.key === 'Escape' && !mutation.isPending) onCancel();
      }}
    >
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          {fields.length ? (
            <TabsTrigger value="form" disabled={changed && tab !== 'form'}>
              Form
            </TabsTrigger>
          ) : null}
          <TabsTrigger value="source" disabled={changed && tab !== 'source'}>
            Source
          </TabsTrigger>
        </TabsList>
        <TabsContent value="form" className="grid gap-2.5">
          {fields.map((field, index) => {
            const id = `${row.rowId}-${field.name.toLowerCase()}`;
            const Control = field.multiline ? Textarea : Input;
            return (
              <div key={field.name} className="grid gap-1">
                <Label htmlFor={id} className="text-[0.68rem] tracking-[0.16em] text-secondary-80 uppercase">
                  {field.name === 'speaker_name'
                    ? 'Speaker'
                    : field.name === 'body'
                      ? 'Norwegian'
                      : field.name === 'translation'
                        ? 'Translation'
                        : field.name === 'text'
                          ? 'Text'
                          : field.name === 'title'
                            ? 'Title'
                            : field.name === 'statement'
                              ? 'Rule'
                              : field.name.startsWith('no')
                                ? `Norwegian ${field.name.split('_')[1] ? Number(field.name.split('_')[1]) + 1 : ''}`.trim()
                                : field.name.startsWith('en')
                                  ? `Translation ${field.name.split('_')[1] ? Number(field.name.split('_')[1]) + 1 : ''}`.trim()
                                  : field.name.replaceAll('_', ' ')}
                </Label>
                <Control
                  id={id}
                  lang={field.lang}
                  autoFocus={index === 0}
                  value={values[index]}
                  onChange={(event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
                    setValues((current) =>
                      current.map((value, valueIndex) => (valueIndex === index ? event.target.value : value)),
                    )
                  }
                />
              </div>
            );
          })}
        </TabsContent>
        <TabsContent value="source">
          <Textarea
            aria-label="Block source"
            value={raw}
            onChange={(event) => setRaw(event.target.value)}
            className="min-h-24 font-mono text-xs"
          />
        </TabsContent>
      </Tabs>
      <div className="mt-3 flex gap-2">
        <Button size="sm" disabled={mutation.isPending} onClick={done}>
          {mutation.isPending ? 'Writing…' : 'Done'}
        </Button>
        <Button size="sm" variant="outline" disabled={mutation.isPending} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </fieldset>
  );
}
