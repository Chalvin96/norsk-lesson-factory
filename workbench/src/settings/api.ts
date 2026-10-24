/** Entry points: workbench settings query, save, and reset used by the settings page. */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import type { WorkbenchSettings, WorkbenchSettingsResponse } from '@/api/contracts';
import { getJson, writeJson } from '@/api/http';

export function useWorkbenchSettings() {
  return useQuery({ queryKey: ['settings'], queryFn: () => getJson<WorkbenchSettingsResponse>('/api/settings') });
}
export function useSaveWorkbenchSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (settings: WorkbenchSettings) => writeJson<WorkbenchSettingsResponse>('/api/settings', 'PUT', settings),
    onSuccess: (result) => client.setQueryData(['settings'], result),
  });
}
export function useResetWorkbenchSettings() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => writeJson<WorkbenchSettingsResponse>('/api/settings/reset', 'POST', {}),
    onSuccess: (result) => client.setQueryData(['settings'], result),
  });
}
