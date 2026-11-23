import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  root: fileURLToPath(new URL('./seed', import.meta.url)),
  plugins: [react()],
  publicDir: false,
  build: {
    outDir: fileURLToPath(new URL('./dist-seed', import.meta.url)),
    emptyOutDir: true,
    sourcemap: false,
  },
});
