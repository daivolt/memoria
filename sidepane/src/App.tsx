import React from 'react';
import { ProgressSidepane } from './components/ProgressSidepane';

export default function App() {
  return (
    <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '8px 16px', borderBottom: '1px solid rgba(108,92,231,0.2)', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
        <span style={{ fontSize: 16, fontWeight: 700, color: '#818cf8' }}>Memoria</span>
        <span style={{ fontSize: 11, color: 'rgba(148,163,184,0.6)' }}>Progress Sidepane</span>
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        <ProgressSidepane />
      </div>
    </div>
  );
}
