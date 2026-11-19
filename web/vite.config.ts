import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: { output: { manualChunks(id) {
      if (/node_modules[\\/]katex[\\/]/.test(id)) return 'math';
      if (/node_modules[\\/]highlight.js[\\/]/.test(id)) return 'syntax';
      if (/node_modules[\\/](react|react-dom|scheduler)[\\/]/.test(id)) return 'react';
      if (/node_modules[\\/]/.test(id)) return 'vendor';
    } } },
  },
});
