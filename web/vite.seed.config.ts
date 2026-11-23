import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: [{ find: /^\.\/workspace-entry$/, replacement: fileURLToPath(new URL('./src/seed/RegisteredApp.tsx', import.meta.url)) }],
  },
  publicDir: false,
  build: {
    outDir: fileURLToPath(new URL('./dist-seed', import.meta.url)),
    emptyOutDir: true,
    sourcemap: false,
  },
});
