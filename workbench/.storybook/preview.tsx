import type { Preview } from '@storybook/react-vite';

import { TooltipProvider } from '../src/components/ui/tooltip';
import '../src/styles/index.css';

const desktop = (width: number, height: number) => ({
  name: `Desktop ${width}`,
  styles: { width: `${width}px`, height: `${height}px` },
  type: 'desktop' as const,
});

const preview: Preview = {
  decorators: [
    (Story) => (
      <TooltipProvider>
        <Story />
      </TooltipProvider>
    ),
  ],
  parameters: {
    layout: 'padded',
    a11y: { test: 'error' },
    viewport: {
      options: {
        mobile320: {
          name: 'Mobile 320',
          styles: { width: '320px', height: '800px' },
          type: 'mobile',
        },
        desktop1280: desktop(1280, 800),
        desktop1440: desktop(1440, 900),
        desktop1920: desktop(1920, 1080),
      },
    },
  },
  initialGlobals: {
    viewport: { value: 'desktop1440', isRotated: false },
  },
};

export default preview;
