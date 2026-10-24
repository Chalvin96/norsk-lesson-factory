/** Entry point: `NavigationGuard` protects registered author edits before navigation. */
import * as React from 'react';

import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';

import {
  cancelPendingNavigation,
  confirmPendingNavigation,
  getPendingNavigation,
  subscribePendingNavigation,
} from './router';

export function NavigationGuard() {
  const pending = React.useSyncExternalStore(subscribePendingNavigation, getPendingNavigation, () => null);

  return (
    <Dialog open={pending !== null} onOpenChange={(open) => !open && cancelPendingNavigation()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Leave with unsaved edits?</DialogTitle>
          <DialogDescription>
            Your staged author edits will be lost if you leave this page. Stay here to keep editing, or discard the
            edits and continue.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="dialogSecondary" onClick={cancelPendingNavigation}>
            Stay here
          </Button>
          <Button variant="destructive" onClick={confirmPendingNavigation}>
            Discard and continue
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
