/**
 * TripleTen Cloud Platform — Incident War Room
 * ======================================================
 * Component:        main.tsx
 * Purpose:          Root React entrypoint mounting App component.
 * Interacts With:   index.html
 * 
 * Curriculum Project: Project 1 — Diagnostics & Telemetry
 * Skills:           React 18 Bootstrap, StrictMode Rendering
 * Tools:            React 18, Vite, TypeScript
 */

import React from 'react';
import ReactDOM from 'react-dom/client';
import '@fontsource-variable/manrope';
import '@fontsource-variable/inter';
import '@fontsource/ibm-plex-mono/400.css';
import App from './App';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
