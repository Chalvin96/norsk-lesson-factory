import { useQueryClient } from '@tanstack/react-query';
import * as React from 'react';
import type { NewLessonRequest } from '@/api/contracts';
import { ApiError, getJson, writeJson } from '@/api/http';
import { WorkbenchJobError } from './use-workbench-job';

const storageKey = 'workbench-new-lesson-jobs';
type Progress = { status: 'idle' | 'pending' | 'success'; error: null } | { status: 'error'; error: Error };
type Session = {
  id: string;
  selectedId: string | undefined;
  request: NewLessonRequest | null;
  isEditing: boolean;
  jobId: string | null;
  progress: Progress;
  steps: string[];
  checkErrors: string[];
  findings: string[];
};

function restoreSessions(): Session[] {
  try {
    const stored: unknown = JSON.parse(sessionStorage.getItem(storageKey) ?? '[]');
    if (!Array.isArray(stored)) return [];
    return stored.flatMap((item: unknown) => {
      if (
        !item ||
        typeof item !== 'object' ||
        !('jobId' in item) ||
        typeof item.jobId !== 'string' ||
        !('request' in item)
      )
        return [];
      const request = item.request;
      if (
        !request ||
        typeof request !== 'object' ||
        !('title' in request) ||
        typeof request.title !== 'string' ||
        !('slug' in request) ||
        typeof request.slug !== 'string' ||
        !('learner_outcome' in request) ||
        typeof request.learner_outcome !== 'string' ||
        !('cefr_level' in request) ||
        !('after_lesson_id' in request) ||
        !(request.after_lesson_id === null || typeof request.after_lesson_id === 'string')
      )
        return [];
      // Narrow the level at the storage boundary, keeping the request wire-shaped.
      const level = request.cefr_level;
      if (level !== 'A1' && level !== 'A2' && level !== 'B1' && level !== 'B2' && level !== 'C1' && level !== 'C2')
        return [];
      return [
        {
          id: crypto.randomUUID(),
          selectedId: undefined,
          request: {
            title: request.title,
            slug: request.slug,
            learner_outcome: request.learner_outcome,
            cefr_level: level,
            after_lesson_id: request.after_lesson_id,
          },
          isEditing: false,
          jobId: item.jobId,
          progress: { status: 'pending', error: null },
          steps: [],
          checkErrors: [],
          findings: [],
        } satisfies Session,
      ];
    });
  } catch {
    return [];
  }
}

function useJobStore() {
  const client = useQueryClient();
  const [sessions, setSessions] = React.useState<Session[]>(restoreSessions);
  const [announcement, setAnnouncement] = React.useState('');
  const isAlive = React.useRef(true);
  const polling = React.useRef(new Set<string>());
  React.useEffect(() => {
    isAlive.current = true;
    return () => {
      isAlive.current = false;
    };
  }, []);
  const update = React.useCallback((id: string, patch: Partial<Session>) => {
    if (isAlive.current)
      setSessions((current) => current.map((session) => (session.id === id ? { ...session, ...patch } : session)));
  }, []);
  React.useEffect(() => {
    try {
      sessionStorage.setItem(
        storageKey,
        JSON.stringify(sessions.filter((session) => session.jobId).map(({ jobId, request }) => ({ jobId, request }))),
      );
    } catch {
      // Storage can be unavailable; in-app tracking still works.
    }
  }, [sessions]);
  React.useEffect(() => {
    for (const session of sessions) {
      const jobId = session.jobId;
      if (!jobId || session.progress.status !== 'pending' || polling.current.has(jobId)) continue;
      polling.current.add(jobId);
      const poll = async () => {
        try {
          while (isAlive.current) {
            const job: unknown = await getJson(`/api/jobs/${encodeURIComponent(jobId)}`);
            if (!isAlive.current) return;
            if (!job || typeof job !== 'object' || !('status' in job)) throw new Error('Invalid job response.');
            const steps =
              'steps' in job && Array.isArray(job.steps)
                ? job.steps.filter((step): step is string => typeof step === 'string')
                : [];
            update(session.id, { steps });
            if (job.status === 'done' || job.status === 'cancelled' || job.status === 'failed') {
              await client.invalidateQueries({ queryKey: ['curriculum'], refetchType: 'all' });
              if (!isAlive.current) return;
              if (job.status === 'cancelled') {
                setSessions((current) => current.filter((item) => item.id !== session.id));
                return;
              }
              if (job.status === 'failed') {
                const checkErrors =
                  'check' in job &&
                  job.check &&
                  typeof job.check === 'object' &&
                  'errors' in job.check &&
                  Array.isArray(job.check.errors)
                    ? job.check.errors.filter((error): error is string => typeof error === 'string')
                    : [];
                update(session.id, {
                  jobId: null,
                  checkErrors,
                  progress: {
                    status: 'error',
                    error: new WorkbenchJobError(
                      'error' in job && typeof job.error === 'string'
                        ? job.error
                        : 'The workbench could not finish this job.',
                      'error_detail' in job && typeof job.error_detail === 'string' ? job.error_detail : null,
                    ),
                  },
                });
              } else {
                setAnnouncement(`Draft ready: ${session.request?.title ?? 'New lesson'}`);
                const findings =
                  'result' in job &&
                  job.result &&
                  typeof job.result === 'object' &&
                  'findings' in job.result &&
                  Array.isArray(job.result.findings)
                    ? job.result.findings.flatMap((finding: unknown) =>
                        finding &&
                        typeof finding === 'object' &&
                        'explanation' in finding &&
                        typeof finding.explanation === 'string'
                          ? [finding.explanation]
                          : [],
                      )
                    : [];
                update(session.id, {
                  jobId: null,
                  findings,
                  progress: { status: 'success', error: null },
                });
              }
              return;
            }
            if (job.status !== 'running' && job.status !== 'queued') throw new Error('Unknown job status.');
            await new Promise<void>((resolve) => window.setTimeout(resolve, 1000));
          }
        } catch (error) {
          update(session.id, {
            ...(error instanceof ApiError && error.status === 404 ? { jobId: null } : {}),
            progress: {
              status: 'error',
              error:
                error instanceof ApiError && error.status === 404
                  ? new Error('The job is no longer on the server; it may have been lost when the server restarted.')
                  : error instanceof Error
                    ? error
                    : new Error(String(error)),
            },
          });
        } finally {
          polling.current.delete(jobId);
        }
      };
      void poll();
    }
  }, [sessions, client, update]);
  const start = async (id: string, request: NewLessonRequest) => {
    update(id, {
      request,
      isEditing: false,
      jobId: null,
      progress: { status: 'pending', error: null },
      steps: [],
      checkErrors: [],
      findings: [],
    });
    try {
      const response: unknown = await writeJson('/api/curriculum/lessons', 'POST', request);
      if (!response || typeof response !== 'object' || !('job_id' in response) || typeof response.job_id !== 'string')
        throw new Error('The server did not return a job id.');
      update(id, { jobId: response.job_id });
    } catch (error) {
      update(id, { progress: { status: 'error', error: error instanceof Error ? error : new Error(String(error)) } });
    }
  };
  return {
    sessions,
    announcement,
    add: (selectedId: string | undefined) =>
      setSessions((current) => [
        ...current,
        {
          id: crypto.randomUUID(),
          selectedId,
          request: null,
          isEditing: true,
          jobId: null,
          progress: { status: 'idle', error: null },
          steps: [],
          checkErrors: [],
          findings: [],
        },
      ]),
    getState: (session: Session) => ({
      dismiss: () => setSessions((current) => current.filter((item) => item.id !== session.id)),
      close: () => {
        if (session.isEditing || session.progress.status === 'success' || session.progress.status === 'idle')
          setSessions((current) => current.filter((item) => item.id !== session.id));
      },
      inputs: session.request,
      request: session.isEditing ? null : session.request,
      setRequest: (request: NewLessonRequest | null) =>
        update(session.id, request ? { request, isEditing: false } : { isEditing: true }),
      job: {
        isPending: session.progress.status === 'pending',
        isSuccess: session.progress.status === 'success',
        isError: session.progress.status === 'error',
        error: session.progress.error ?? new Error('The workbench could not finish this job.'),
        steps: session.steps,
        checkErrors: session.checkErrors,
        findings: session.findings,
        mutate: ({ body }: { route: string; body: NewLessonRequest }) => {
          void start(session.id, body);
        },
        retry: () => {
          if (session.jobId) {
            update(session.id, { progress: { status: 'pending', error: null } });
          } else if (session.request) void start(session.id, session.request);
        },
      },
    }),
  };
}

const Context = React.createContext<ReturnType<typeof useJobStore> | null>(null);
export function NewLessonJobsProvider({ children }: { children: React.ReactNode }) {
  const store = useJobStore();
  return (
    <Context.Provider value={store}>
      <p role="status" aria-label="Lesson job announcements" aria-live="polite" aria-atomic="true" className="sr-only">
        {store.announcement}
      </p>
      {children}
    </Context.Provider>
  );
}
export function useNewLessonJobs() {
  const store = React.useContext(Context);
  if (!store) throw new Error('New lesson jobs require NewLessonJobsProvider.');
  return store;
}
export type NewLessonJob = ReturnType<ReturnType<typeof useJobStore>['getState']>;
