import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);

export default defineConfig({
  plugins: [react()],
  define: { global: 'globalThis' },
  resolve: {
    alias: {
      buffer: require.resolve('buffer/'),
      events: require.resolve('events/'),
      process: require.resolve('process/browser'),
      stream: require.resolve('stream-browserify'),
    },
  },
  server: {
    port: 5183,
    strictPort: true,
    proxy: {
      '/api': `http://localhost:${process.env.BACKEND_PORT || 8010}`,
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return;
          if (id.includes('@leafygreen-ui') || id.includes('@lg-')) return 'leafygreen';
          if (id.includes('@emotion')) return 'emotion';
          if (/node_modules\/(react|react-dom|scheduler)\//.test(id)) return 'react';
          return 'vendor';
        },
      },
    },
  },
});
