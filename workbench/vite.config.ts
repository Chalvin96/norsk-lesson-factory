import path from 'node:path';
import { fileURLToPath } from 'node:url';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

const dirname = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': path.resolve(dirname, './src') },
  },
  build: {
    outDir: path.resolve(dirname, '../src/lesson_builder/web/app'),
    emptyOutDir: true,
    assetsDir: 'assets',
  },
  server: {
    proxy: { '/api': `http://127.0.0.1:${process.env.WORKBENCH_API_PORT ?? '8765'}` },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
