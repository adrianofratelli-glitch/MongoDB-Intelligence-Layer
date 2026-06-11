import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { nodePolyfills } from 'vite-plugin-node-polyfills';

export default defineConfig({
  // @emotion/server (dependência transitiva do LeafyGreen) usa builtins do Node
  plugins: [react(), nodePolyfills()],
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
});
