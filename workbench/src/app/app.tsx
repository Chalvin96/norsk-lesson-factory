/** Entry points: `App` renders the active workbench route; `createQueryClient` is App's default client factory. */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import * as React from 'react';

import { Toaster } from '@/components/ui/sonner';
import { TooltipProvider } from '@/components/ui/tooltip';
import { PageStatus } from '@/components/workbench/page-status';
import { CurriculumPage } from '@/curriculum/curriculum-page';
import { NewLessonJobsProvider } from '@/jobs/new-lesson-jobs';
import type { LessonRouteState } from '@/lesson/lesson-ui';
import { AppShell } from './app-shell';
import { NavigationGuard } from './navigation-guard';
import { loadLessonRoute, useRoute } from './router';

const SettingsPage = React.lazy(() =>
  import('@/settings/settings-page').then(({ SettingsPage }) => ({ default: SettingsPage })),
);
const LazyLessonPage = React.lazy(() => loadLessonRoute().then(({ LessonPage }) => ({ default: LessonPage })));

type LessonPageComponent = React.ComponentType<{ lessonId: string; routeState: LessonRouteState }>;

export function createQueryClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } } });
}

export function App({
  queryClient,
  lessonPageComponent,
}: {
  queryClient?: QueryClient;
  lessonPageComponent?: LessonPageComponent;
}) {
  const [client] = React.useState(() => queryClient ?? createQueryClient());
  const route = useRoute();
  const ActiveLessonPage = lessonPageComponent ?? LazyLessonPage;
  React.useEffect(() => {
    document.title =
      route.page === 'curriculum'
        ? 'Curriculum · Workbench · norsk lesson factory'
        : route.page === 'settings'
          ? 'Settings · Workbench · norsk lesson factory'
          : 'Workbench · norsk lesson factory';
  }, [route]);
  return (
    <QueryClientProvider client={client}>
      <NewLessonJobsProvider>
        <TooltipProvider>
          <AppShell route={route}>
            {route.page === 'curriculum' ? <CurriculumPage /> : null}
            {route.page === 'lesson' ? (
              <React.Suspense fallback={<PageStatus>Loading lesson…</PageStatus>}>
                <ActiveLessonPage lessonId={route.lessonId} routeState={route.ui} />
              </React.Suspense>
            ) : null}
            {route.page === 'settings' ? (
              <React.Suspense fallback={<PageStatus>Loading settings…</PageStatus>}>
                <SettingsPage />
              </React.Suspense>
            ) : null}
            {route.page === 'not-found' ? <PageStatus tone="error">Page not found.</PageStatus> : null}
          </AppShell>
          <NavigationGuard />
          <Toaster />
        </TooltipProvider>
      </NewLessonJobsProvider>
    </QueryClientProvider>
  );
}
