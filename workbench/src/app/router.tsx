/** Entry points: `parseRoute` and `useRoute` read routes; `Link` and `navigate` handle navigation; `loadLessonRoute` and `preloadLessonRoute` load the lazy lesson page for `App` and navigation intent. */

import * as React from 'react';

import { type LessonRouteState, parseLessonRouteState, writeLessonRouteState } from '@/lesson/lesson-ui';

export type Route =
  | { page: 'curriculum' }
  | { page: 'lesson'; lessonId: string; ui: LessonRouteState }
  | { page: 'settings' }
  | { page: 'not-found' };

const LESSON_PREFIX = '/lessons/';
const K_ROUTER_HISTORY_INDEX = '__lessonWorkbenchHistoryIndex';
let lessonRoutePromise: Promise<typeof import('@/lesson/lesson-page')> | null = null;

export function loadLessonRoute(): Promise<typeof import('@/lesson/lesson-page')> {
  if (!lessonRoutePromise) {
    lessonRoutePromise = import('@/lesson/lesson-page').catch((error: unknown) => {
      lessonRoutePromise = null;
      throw error;
    });
  }
  return lessonRoutePromise;
}

export function preloadLessonRoute(): void {
  void loadLessonRoute().catch(() => undefined);
}

export function parseRoute(pathname: string, search = ''): Route {
  if (pathname === '/') return { page: 'curriculum' };
  if (pathname === '/settings') return { page: 'settings' };
  if (pathname.startsWith(LESSON_PREFIX)) {
    let lessonId: string;
    try {
      lessonId = decodeURIComponent(pathname.slice(LESSON_PREFIX.length));
    } catch {
      return { page: 'not-found' };
    }
    if (/^[a-z0-9][a-z0-9_-]*$/.test(lessonId)) return { page: 'lesson', lessonId, ui: parseLessonRouteState(search) };
  }
  return { page: 'not-found' };
}

export function lessonHref(lessonId: string): string {
  return `${LESSON_PREFIX}${encodeURIComponent(lessonId)}`;
}

const listeners = new Set<() => void>();
const navigationListeners = new Set<() => void>();
const navigationWork = new Map<symbol, { discard: (() => void) | undefined }>();

type PendingNavigation = {
  href: string;
  kind: 'link' | 'history';
  targetIndex?: number;
};

let pendingNavigation: PendingNavigation | null = null;
let knownHref: string | null = null;
let historyIndex = 0;
let restoringNavigation: PendingNavigation | null = null;
let allowedHistoryHref: string | null = null;
let routerHistoryInstalled = false;

function currentHref(): string {
  return `${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function normalizedHref(href: string): string {
  const destination = new URL(href, window.location.href);
  return `${destination.pathname}${destination.search}${destination.hash}`;
}

function stateWithHistoryIndex(state: unknown, index: number): Record<string, unknown> {
  const base = state && typeof state === 'object' ? (state as Record<string, unknown>) : {};
  return { ...base, [K_ROUTER_HISTORY_INDEX]: index };
}

function readHistoryIndex(state: unknown): number | undefined {
  if (!state || typeof state !== 'object') return undefined;
  const index = (state as Record<string, unknown>)[K_ROUTER_HISTORY_INDEX];
  return typeof index === 'number' ? index : undefined;
}

function hasUnsavedNavigationWork(): boolean {
  return navigationWork.size > 0;
}

function notifyNavigationListeners() {
  navigationListeners.forEach((listener) => {
    listener();
  });
}

function notifyRouteListeners() {
  listeners.forEach((listener) => {
    listener();
  });
}

function onPopState(event: PopStateEvent) {
  const destination = currentHref();
  const destinationIndex = readHistoryIndex(event.state);

  if (allowedHistoryHref === destination) {
    allowedHistoryHref = null;
    knownHref = destination;
    historyIndex = destinationIndex ?? historyIndex;
    notifyRouteListeners();
    return;
  }

  if (restoringNavigation && destination === knownHref) {
    historyIndex = destinationIndex ?? historyIndex;
    pendingNavigation = restoringNavigation;
    restoringNavigation = null;
    notifyNavigationListeners();
    return;
  }

  if (destination === knownHref) {
    historyIndex = destinationIndex ?? historyIndex;
    return;
  }

  if (hasUnsavedNavigationWork()) {
    const attempt: PendingNavigation = {
      href: destination,
      kind: 'history',
      ...(destinationIndex === undefined ? {} : { targetIndex: destinationIndex }),
    };
    if (destinationIndex !== undefined) {
      restoringNavigation = attempt;
      window.history.go(historyIndex - destinationIndex);
      return;
    }

    window.history.replaceState(stateWithHistoryIndex(event.state, historyIndex), '', knownHref ?? '/');
    pendingNavigation = attempt;
    notifyNavigationListeners();
    return;
  }

  knownHref = destination;
  historyIndex = destinationIndex ?? historyIndex;
  notifyRouteListeners();
}

function onBeforeUnload(event: BeforeUnloadEvent) {
  if (!hasUnsavedNavigationWork()) return;
  event.preventDefault();
  event.returnValue = '';
}

function installRouterHistory() {
  if (routerHistoryInstalled) return;
  routerHistoryInstalled = true;
  knownHref = currentHref();
  historyIndex = 0;
  window.history.replaceState(stateWithHistoryIndex(window.history.state, historyIndex), '', knownHref);
  window.addEventListener('popstate', onPopState);
  window.addEventListener('beforeunload', onBeforeUnload);
}

function synchronizeRouterHistory() {
  installRouterHistory();
  const href = currentHref();
  if (href === knownHref || pendingNavigation || restoringNavigation) return;
  knownHref = href;
  historyIndex = 0;
  window.history.replaceState(stateWithHistoryIndex(window.history.state, historyIndex), '', href);
}

function commitNavigation(href: string) {
  historyIndex += 1;
  window.history.pushState(stateWithHistoryIndex(window.history.state, historyIndex), '', href);
  knownHref = href;
  notifyRouteListeners();
}

export function registerNavigationWork(token: symbol, dirty: boolean, discard?: () => void): () => void {
  installRouterHistory();
  if (dirty) navigationWork.set(token, { discard });
  else navigationWork.delete(token);
  return () => {
    navigationWork.delete(token);
  };
}

export function useUnsavedNavigationWork(dirty: boolean, discard?: () => void) {
  const token = React.useRef(Symbol('unsaved-navigation-work'));
  React.useEffect(() => registerNavigationWork(token.current, dirty, discard), [dirty, discard]);
}

export function subscribePendingNavigation(listener: () => void): () => void {
  navigationListeners.add(listener);
  return () => navigationListeners.delete(listener);
}

export function getPendingNavigation(): PendingNavigation | null {
  return pendingNavigation;
}

export function cancelPendingNavigation() {
  pendingNavigation = null;
  notifyNavigationListeners();
}

export function confirmPendingNavigation() {
  const attempt = pendingNavigation;
  if (!attempt) return;
  navigationWork.forEach(({ discard }) => {
    discard?.();
  });
  pendingNavigation = null;
  notifyNavigationListeners();
  if (attempt.kind === 'history' && attempt.targetIndex !== undefined) {
    allowedHistoryHref = attempt.href;
    const distance = attempt.targetIndex - historyIndex;
    if (distance !== 0) {
      window.history.go(distance);
      return;
    }
  }
  commitNavigation(attempt.href);
}

export function navigate(href: string) {
  synchronizeRouterHistory();
  const destination = normalizedHref(href);
  if (destination === currentHref()) return;
  if (hasUnsavedNavigationWork()) {
    pendingNavigation = { href: destination, kind: 'link' };
    notifyNavigationListeners();
    return;
  }
  commitNavigation(destination);
}

export function updateLessonRouteState(patch: Partial<LessonRouteState>) {
  synchronizeRouterHistory();
  if (pendingNavigation || restoringNavigation) return;
  const route = parseRoute(window.location.pathname, window.location.search);
  if (route.page !== 'lesson') return;
  const destination = new URL(window.location.href);
  destination.search = writeLessonRouteState(new URLSearchParams(destination.search), {
    ...route.ui,
    ...patch,
  }).toString();
  const href = `${destination.pathname}${destination.search}${destination.hash}`;
  if (href !== currentHref()) commitNavigation(href);
}

function subscribe(listener: () => void) {
  synchronizeRouterHistory();
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function useRoute(): Route {
  const location = React.useSyncExternalStore(subscribe, () => `${window.location.pathname}${window.location.search}`);
  const separator = location.indexOf('?');
  const pathname = separator < 0 ? location : location.slice(0, separator);
  const search = separator < 0 ? '' : location.slice(separator + 1);
  return React.useMemo(() => parseRoute(pathname, search), [pathname, search]);
}

export function Link({
  href,
  onClick,
  onFocus,
  onPointerEnter,
  ...props
}: React.ComponentProps<'a'> & { href: string }) {
  const preloadOnIntent = () => {
    const destination = new URL(href, window.location.href);
    if (parseRoute(destination.pathname).page === 'lesson') preloadLessonRoute();
  };
  return (
    <a
      href={href}
      onPointerEnter={(event) => {
        onPointerEnter?.(event);
        preloadOnIntent();
      }}
      onFocus={(event) => {
        onFocus?.(event);
        preloadOnIntent();
      }}
      onClick={(event) => {
        onClick?.(event);
        if (
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey
        )
          return;
        event.preventDefault();
        navigate(href);
      }}
      {...props}
    />
  );
}
