import { fileURLToPath, URL } from 'node:url';
import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  define: { "import.meta.env.VITE_REGISTERED": JSON.stringify(true) },
  resolve: {
    alias: [
      { find: /^\.\/workspace-entry$/, replacement: fileURLToPath(new URL('./src/seed/RegisteredApp.tsx', import.meta.url)) },
      { find: /^\.\/legacyBank$/, replacement: fileURLToPath(new URL('./src/seed/legacyBank.ts', import.meta.url)) },
      { find: /^\.\/localTransport$/, replacement: fileURLToPath(new URL('./src/seed/noLocalTransport.ts', import.meta.url)) },
    ],
  },
  build: {
    outDir: fileURLToPath(new URL('./dist-seed', import.meta.url)),
    assetsDir: 'static-assets',
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      input: [
        fileURLToPath(new URL('./index.html', import.meta.url)),
        fileURLToPath(new URL('./podcast-offline.html', import.meta.url)),
      ],
    },
  },
});
