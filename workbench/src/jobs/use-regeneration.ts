/** Entry point: `useRegeneration` for lesson lifecycle regeneration jobs. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as React from 'react';
import type { RegenerationRun } from '@/api/contracts';
import { getJson, writeJson } from '@/api/http';

type RegenerationRequest = { mode: 'scope'; key: string } | { mode: 'exercises'; exerciseKeys: string[] };
export function useRegeneration(lessonId: string) {
  const client = useQueryClient();
  const [runId, setRunId] = React.useState<string | null>(() => sessionStorage.getItem(`regeneration-${lessonId}`));
  const [jobId, setJobId] = React.useState<string | null>(null);
  React.useEffect(() => {
    const refresh = () => setRunId(sessionStorage.getItem(`regeneration-${lessonId}`));
    window.addEventListener('workbench-regeneration', refresh);
    return () => window.removeEventListener('workbench-regeneration', refresh);
  }, [lessonId]);
  const start = useMutation({
    mutationFn: (request: RegenerationRequest) => {
      const body =
        request.mode === 'exercises'
          ? { mode: 'exercises', exercise_keys: request.exerciseKeys }
          : { mode: 'scope', key: request.key };
      return writeJson<{ run_id: string; job_id: string }>(
        `/api/lessons/${encodeURIComponent(lessonId)}/regenerate`,
        'POST',
        body,
      );
    },
    onSuccess: (result) => {
      setRunId(result.run_id);
      setJobId(result.job_id);
      sessionStorage.setItem(`regeneration-${lessonId}`, result.run_id);
    },
  });
  const run = useQuery({
    queryKey: ['run', runId],
    enabled: runId !== null,
    queryFn: () => getJson<RegenerationRun>(`/api/runs/${encodeURIComponent(runId ?? '')}`),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'done' || status === 'failed' || status === 'cancelled' || status === 'shutdown_interrupted'
        ? false
        : 1000;
    },
  });
  const cancel = useMutation({
    mutationFn: async () => {
      const activeJobId = run.data?.job_id ?? jobId;
      if (!activeJobId) throw new Error('The active regeneration job could not be found.');
      return writeJson(`/api/jobs/${encodeURIComponent(activeJobId)}/cancel`, 'POST', {});
    },
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: ['run', runId], refetchType: 'all' });
    },
  });
  React.useEffect(() => {
    if (run.data?.status === 'waiting' || run.data?.status === 'done')
      void client.invalidateQueries({ queryKey: ['lesson', lessonId] });
  }, [client, lessonId, run.data?.status]);
  React.useEffect(() => {
    if (
      run.data?.status === 'done' ||
      run.data?.status === 'failed' ||
      run.data?.status === 'cancelled' ||
      run.data?.status === 'shutdown_interrupted'
    ) {
      setJobId(null);
    }
  }, [run.data?.status]);
  return { start, run, cancel, jobId: run.data?.job_id ?? jobId };
}
