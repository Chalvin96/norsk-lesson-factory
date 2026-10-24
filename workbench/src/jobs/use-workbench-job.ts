/** Entry points: `useWorkbenchJob` and `hasActiveChatTurn` for shared workbench jobs. */

import { useMutation, useQueryClient } from '@tanstack/react-query';
import * as React from 'react';

import { ApiError, getJson, writeJson } from '@/api/http';
import type { ActiveTurnData, ChatTurnData } from '@/chat/types';

type ActiveChatTurn = {
  turnId: string;
  status: 'pending' | 'running';
  partial: string;
};

export class WorkbenchJobError extends Error {
  constructor(
    message: string,
    readonly detail: string | null,
  ) {
    super(message);
    this.name = 'WorkbenchJobError';
  }
}

export function useWorkbenchJob(
  queryKey: string[],
  persistedTurns: readonly ChatTurnData[] = [],
  persistedActiveTurn?: ActiveTurnData | null,
) {
  const client = useQueryClient();
  const activeTurn = persistedActiveTurn ?? findActiveChatTurn(persistedTurns);
  const ticket = React.useRef<{ route: string; body: object; jobId: string | null; turnId: string | null } | null>(
    null,
  );
  const [steps, setSteps] = React.useState<string[]>([]);
  const [findings, setFindings] = React.useState<string[]>([]);
  const [checkErrors, setCheckErrors] = React.useState<string[]>([]);
  const [startedAt, setStartedAt] = React.useState<number | null>(null);
  const [partial, setPartial] = React.useState('');
  const [turnId, setTurnId] = React.useState<string | null>(null);
  const [cancelled, setCancelled] = React.useState(false);
  const [restoredProgress, setRestoredProgress] = React.useState<{ turnId: string; startedAt: number } | null>(null);
  const mutation = useMutation({
    mutationFn: async ({ route, body }: { route: string; body: object }) => {
      if (!ticket.current || ticket.current.route !== route || ticket.current.body !== body) {
        ticket.current = { route, body, jobId: null, turnId: null };
      }
      setStartedAt(Date.now());
      setPartial('');
      setTurnId(null);
      setCancelled(false);
      setSteps([]);
      setCheckErrors([]);
      setFindings([]);
      const request = ticket.current;
      if (request.jobId === null) {
        const response: unknown = await writeJson(route, 'POST', body);
        if (
          !response ||
          typeof response !== 'object' ||
          !('job_id' in response) ||
          typeof response.job_id !== 'string'
        ) {
          throw new Error('The server did not return a job id.');
        }
        request.jobId = response.job_id;
        if ('turn_id' in response && typeof response.turn_id === 'string') {
          request.turnId = response.turn_id;
          setTurnId(response.turn_id);
        }
      }
      for (;;) {
        let job: unknown;
        try {
          job = await getJson(`/api/jobs/${encodeURIComponent(request.jobId)}`);
        } catch (error) {
          if (error instanceof ApiError && error.status === 404) request.jobId = null;
          throw error;
        }
        if (!job || typeof job !== 'object' || !('status' in job)) throw new Error('Invalid job response.');
        if ('run_id' in job && typeof job.run_id === 'string' && queryKey[0] === 'lesson') {
          sessionStorage.setItem(`regeneration-${queryKey[1]}`, job.run_id);
          window.dispatchEvent(new Event('workbench-regeneration'));
        }
        if ('steps' in job && Array.isArray(job.steps))
          setSteps(job.steps.filter((step): step is string => typeof step === 'string'));
        if ('partial' in job && typeof job.partial === 'string') setPartial(job.partial);
        if ('turn_id' in job && typeof job.turn_id === 'string') {
          request.turnId = job.turn_id;
          setTurnId(job.turn_id);
        }
        if (job.status === 'failed') {
          if (
            'check' in job &&
            job.check &&
            typeof job.check === 'object' &&
            'errors' in job.check &&
            Array.isArray(job.check.errors)
          ) {
            setCheckErrors(job.check.errors.filter((error): error is string => typeof error === 'string'));
          }
          await client.invalidateQueries({ queryKey });
          setStartedAt(null);
          request.jobId = null;
          throw new WorkbenchJobError(
            'error' in job && typeof job.error === 'string' ? job.error : 'The workbench could not finish this job.',
            'error_detail' in job && typeof job.error_detail === 'string' ? job.error_detail : null,
          );
        }
        if (job.status === 'cancelled') {
          await client.invalidateQueries({ queryKey, refetchType: 'all' });
          setCancelled(true);
          setPartial('');
          setStartedAt(null);
          ticket.current = null;
          return;
        }
        if (job.status === 'done') {
          if (
            'result' in job &&
            job.result &&
            typeof job.result === 'object' &&
            'findings' in job.result &&
            Array.isArray(job.result.findings)
          ) {
            setFindings(
              job.result.findings.flatMap((finding: unknown) =>
                finding &&
                typeof finding === 'object' &&
                'explanation' in finding &&
                typeof finding.explanation === 'string'
                  ? [finding.explanation]
                  : [],
              ),
            );
          }
          await client.invalidateQueries({ queryKey, refetchType: 'all' });
          setPartial('');
          setTurnId(null);
          setStartedAt(null);
          ticket.current = null;
          return;
        }
        if (job.status !== 'queued' && job.status !== 'running') throw new Error('Unknown job status.');
        await new Promise<void>((resolve) => window.setTimeout(resolve, 1000));
      }
    },
  });
  React.useEffect(() => {
    setRestoredProgress((current) => {
      if (!activeTurn) return null;
      return current?.turnId === activeTurn.turnId ? current : { turnId: activeTurn.turnId, startedAt: Date.now() };
    });
  }, [activeTurn]);
  const restoredStartedAt =
    activeTurn && restoredProgress?.turnId === activeTurn.turnId ? restoredProgress.startedAt : null;
  return {
    ...mutation,
    startedAt: mutation.isPending ? startedAt : restoredStartedAt,
    steps,
    checkErrors,
    findings,
    partial: mutation.isPending ? partial : (activeTurn?.partial ?? ''),
    turnId: turnId ?? activeTurn?.turnId ?? null,
    cancelled,
    cancel: async () => {
      const request = ticket.current;
      const activeTurnId = request?.turnId ?? activeTurn?.turnId;
      if (activeTurnId) {
        const route =
          queryKey[0] === 'curriculum'
            ? '/api/curriculum/chat'
            : `/api/lessons/${encodeURIComponent(queryKey[1] ?? '')}/chat`;
        await writeJson(`${route}/${encodeURIComponent(activeTurnId)}/cancel`, 'POST', {});
      } else if (request?.jobId) {
        await writeJson(`/api/jobs/${encodeURIComponent(request.jobId)}/cancel`, 'POST', {});
      }
      await client.invalidateQueries({ queryKey, refetchType: 'all' });
    },
    retryTurn: (retryId: string) => {
      const route =
        queryKey[0] === 'curriculum'
          ? '/api/curriculum/chat'
          : `/api/lessons/${encodeURIComponent(queryKey[1] ?? '')}/chat`;
      mutation.mutate({ route: `${route}/${encodeURIComponent(retryId)}/retry`, body: {} });
    },
    newChat: async () => {
      const route =
        queryKey[0] === 'curriculum'
          ? '/api/curriculum/chat/new'
          : `/api/lessons/${encodeURIComponent(queryKey[1] ?? '')}/chat/new`;
      await writeJson(route, 'POST', {});
      ticket.current = null;
      setPartial('');
      setTurnId(null);
      setCancelled(false);
      await client.invalidateQueries({ queryKey, refetchType: 'all' });
    },
    retry: () => {
      if (ticket.current) mutation.mutate({ route: ticket.current.route, body: ticket.current.body });
    },
  };
}
function findActiveChatTurn(turns: readonly ChatTurnData[] | undefined): ActiveChatTurn | null {
  if (!turns) return null;
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const turn = turns[index];
    if (turn?.role === 'author' && turn.turnId && (turn.status === 'pending' || turn.status === 'running')) {
      return { turnId: turn.turnId, status: turn.status, partial: turn.partial ?? '' };
    }
  }
  return null;
}

export function hasActiveChatTurn(turns: readonly ChatTurnData[] | undefined): boolean {
  return findActiveChatTurn(turns) !== null;
}
