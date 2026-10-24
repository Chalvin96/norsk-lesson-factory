/** Entry points: lesson reads and writes used by lesson workspace and editing features. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { BlockWrite, LessonActionResult, LessonPayload, SaveResult, WriteResult } from '@/api/contracts';
import { getJson, writeJson } from '@/api/http';
import { hasActiveChatTurn } from '@/jobs/use-workbench-job';

export function useLesson(lessonId: string) {
  return useQuery({
    queryKey: ['lesson', lessonId],
    queryFn: () => getJson<LessonPayload>(`/api/lessons/${encodeURIComponent(lessonId)}`),
    refetchInterval: (query) =>
      query.state.data?.state.activeTurn || hasActiveChatTurn(query.state.data?.state.chat) ? 1000 : false,
  });
}
export function useWriteBlock(lessonId: string) {
  const client = useQueryClient();
  return useMutation({
    scope: { id: `write-${lessonId}` },
    mutationFn: ({ key, body }: { key: string; body: BlockWrite }) =>
      writeJson<WriteResult>(
        `/api/lessons/${encodeURIComponent(lessonId)}/blocks/${encodeURIComponent(key)}`,
        'PUT',
        body,
      ),
    onSuccess: (result) => {
      client.setQueryData(['lesson', lessonId], result.lesson);
      void client.invalidateQueries({ queryKey: ['lesson', lessonId] });
      void client.invalidateQueries({ queryKey: ['curriculum'] });
    },
  });
}
export function useWriteFile(lessonId: string) {
  const client = useQueryClient();
  return useMutation({
    scope: { id: `write-${lessonId}` },
    mutationFn: (text: string) =>
      writeJson<WriteResult>(`/api/lessons/${encodeURIComponent(lessonId)}/files/lesson.md`, 'PUT', { text }),
    onSuccess: (result) => {
      client.setQueryData(['lesson', lessonId], result.lesson);
      void client.invalidateQueries({ queryKey: ['lesson', lessonId] });
      void client.invalidateQueries({ queryKey: ['curriculum'] });
    },
  });
}
export function useSaveLesson(lessonId: string) {
  const client = useQueryClient();
  return useMutation({
    scope: { id: `write-${lessonId}` },
    mutationFn: () => writeJson<SaveResult>(`/api/lessons/${encodeURIComponent(lessonId)}/save`, 'POST', {}),
    onSuccess: (result) => {
      client.setQueryData<LessonPayload>(['lesson', lessonId], (lesson) =>
        lesson
          ? {
              ...lesson,
              draft: {
                ...lesson.draft,
                dirty: false,
                lastCommit: result.commit,
              },
            }
          : lesson,
      );
      void client.invalidateQueries({ queryKey: ['lesson', lessonId] });
    },
  });
}
export function useLessonAction(lessonId: string) {
  const client = useQueryClient();
  return useMutation({
    scope: { id: `write-${lessonId}` },
    mutationFn: ({ route, body = {} }: { route: string; body?: object }) =>
      writeJson<LessonActionResult>(`/api/lessons/${encodeURIComponent(lessonId)}/${route}`, 'POST', body),
    onSuccess: (result, { route }) => {
      if (result.lesson) client.setQueryData(['lesson', lessonId], result.lesson);
      void client.invalidateQueries({ queryKey: ['lesson', lessonId] });
      if (!/proposals\/.*\/(accept|reject)$/.test(route)) void client.invalidateQueries({ queryKey: ['curriculum'] });
    },
  });
}
