/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        vite.config.ts
 * Purpose:          Vite build configuration, dev server proxy, and Vitest setup.
 * Interacts With:   incident-agent-api (:8000)
 * 
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           Frontend Bundling, Dev Server Proxying
 * Tools:            Vite, React, TypeScript, Vitest
 */

/// <reference types="vitest" />
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const apiTarget = process.env.VITE_API_TARGET || 'http://localhost:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 3000,
    host: '0.0.0.0',
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './tests/setupTests.ts',
  },
});
