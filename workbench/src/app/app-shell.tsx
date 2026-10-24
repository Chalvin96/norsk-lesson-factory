import { ChevronDownIcon } from 'lucide-react';
import * as React from 'react';
import { Input } from '@/components/ui/input';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover';
import { ScrollArea } from '@/components/ui/scroll-area';
import { StatusChip } from '@/components/workbench';
import { useCurriculum } from '@/curriculum/api';
import { cn } from '@/lib/utils';

import { Link, lessonHref, type Route } from './router';

export const LESSON_LIST_NOTICE = 'Lesson list unavailable: fix content/curriculum/plan.yaml and reload.';

function Tab({ href, current, children }: { href: string | null; current: boolean; children: React.ReactNode }) {
  const className = cn(
    'inline-flex items-center border-b-2 border-transparent px-0.5 font-semibold text-muted-foreground hover:text-foreground',
    current && 'border-primary text-foreground',
  );
  if (!href) {
    return (
      <span aria-disabled="true" className={cn(className, 'opacity-50')}>
        {children}
      </span>
    );
  }
  return (
    <Link href={href} aria-current={current ? 'page' : undefined} className={className}>
      {children}
    </Link>
  );
}

function LessonSwitcher({ lessonId }: { lessonId: string }) {
  const curriculum = useCurriculum();
  const [open, setOpen] = React.useState(false);
  const [search, setSearch] = React.useState('');
  const lessons = curriculum.data?.lessons ?? [];
  const visibleLessons = lessons.filter(
    (lesson) =>
      lesson.source !== 'missing' && lesson.title.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()),
  );
  const current = lessons.find((lesson) => lesson.lessonId === lessonId);
  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setSearch('');
      }}
    >
      <PopoverTrigger asChild>
        <button
          type="button"
          className="flex h-8 w-[clamp(12rem,30vw,28rem)] items-center gap-2.5 overflow-hidden rounded-md border bg-secondary-0 pr-2 pl-1.5 text-left text-sm focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
        >
          <span className="min-w-7 rounded-md bg-primary-10 px-1 text-center text-xs font-bold text-primary-90">
            {current ? String(current.sequenceIndex + 1).padStart(2, '0') : '··'}
          </span>
          <span className="flex-1 truncate font-semibold">{current?.title ?? lessonId}</span>
          <span className="sr-only">Switch lesson</span>
          <ChevronDownIcon aria-hidden className="size-4 text-muted-foreground" />
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[min(30rem,calc(100vw-1rem))] p-0">
        {curriculum.isError ? (
          <p role="status" className="m-2 rounded-md bg-warning-10 px-3 py-2.5 text-sm text-warning-90">
            {LESSON_LIST_NOTICE}
          </p>
        ) : (
          <>
            <div className="border-b p-2">
              <Input
                autoFocus
                aria-label="Search lessons"
                placeholder="Search lessons by title"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </div>
            <ScrollArea className="h-[min(36rem,70vh)]">
              <ol className="m-0 list-none p-1.5">
                {visibleLessons.map((lesson) => (
                  <li key={lesson.lessonId}>
                    <Link
                      href={lessonHref(lesson.lessonId)}
                      onClick={() => setOpen(false)}
                      aria-current={lesson.lessonId === lessonId ? 'page' : undefined}
                      className="flex items-center gap-2.5 rounded-md px-2 py-1.5 text-sm hover:bg-secondary-10 aria-[current=page]:bg-primary-10"
                    >
                      <span
                        aria-hidden
                        className="min-w-7 rounded-md bg-primary-10 px-1 text-center text-xs font-bold text-primary-90"
                      >
                        {String(lesson.sequenceIndex + 1).padStart(2, '0')}
                      </span>
                      <span className="min-w-0 flex-1">{lesson.title}</span>
                      <StatusChip tone={lesson.source === 'draft' ? 'draft' : 'neutral'}>
                        {lesson.source === 'draft' ? 'Draft' : 'Canonical'}
                      </StatusChip>
                    </Link>
                  </li>
                ))}
              </ol>
              {!visibleLessons.length ? (
                <p className="px-3 py-5 text-center text-sm text-muted-foreground">No lessons match that title.</p>
              ) : null}
            </ScrollArea>
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}

export function AppShell({ route, children }: { route: Route; children: React.ReactNode }) {
  const curriculum = useCurriculum();
  const firstLesson = curriculum.data?.lessons[0]?.lessonId;
  const lessonId = route.page === 'lesson' ? route.lessonId : firstLesson;
  return (
    <div className="flex h-dvh min-w-0 flex-col overflow-x-hidden overflow-y-auto bg-background">
      <a
        href="#workbench-main"
        className="fixed top-0 left-3 z-50 -translate-y-[200%] rounded-lg border border-primary-30 bg-white px-3.5 py-2.5 text-primary-100 focus:translate-y-3 focus:shadow-lg"
      >
        Skip to main content
      </a>
      <a
        href="#chat-message"
        className="fixed top-0 left-44 z-50 -translate-y-[200%] rounded-lg border border-primary-30 bg-white px-3.5 py-2.5 text-primary-100 focus:translate-y-3 focus:shadow-lg"
      >
        Skip to chat
      </a>
      <header className="flex min-h-13 shrink-0 flex-wrap items-center gap-x-3 gap-y-2 border-b bg-white px-2 py-2 sm:flex-nowrap sm:gap-6 sm:px-5">
        <div className="flex flex-col">
          <span className="font-display text-lg leading-tight font-semibold">Workbench</span>
          <span className="hidden text-[0.68rem] font-semibold tracking-[0.12em] text-muted-foreground uppercase sm:block">
            norsk lesson factory
          </span>
        </div>
        <nav aria-label="Workbench views" className="flex gap-3 self-stretch sm:gap-5">
          <Tab href="/" current={route.page === 'curriculum'}>
            Curriculum
          </Tab>
          <Tab href={lessonId ? lessonHref(lessonId) : null} current={route.page === 'lesson'}>
            Lesson
          </Tab>
          <Tab href="/settings" current={route.page === 'settings'}>
            Settings
          </Tab>
        </nav>
        {route.page === 'lesson' ? <LessonSwitcher lessonId={route.lessonId} /> : null}
        <div className="ml-auto hidden items-center gap-2 sm:flex">
          <StatusChip>Local · draft editing</StatusChip>
        </div>
      </header>
      {children}
    </div>
  );
}
