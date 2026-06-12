import React from 'react';
import { useDashboardStore } from '../store/dashboardStore';
import { AgentGraph } from './AgentGraph';
import { KanbanStrip } from './KanbanStrip';
import { useDashboardWS } from '../hooks/useDashboardWS';

const STATUS_COLORS: Record<string, string> = {
  active: '#34d399',
  idle: '#fbbf24',
  busy: '#818cf8',
  stale: '#f87171',
  unknown: '#94a3b8',
};

function AgentList() {
  const agents = useDashboardStore((s) => s.agents);
  return (
    <div style={{ display: 'flex', gap: 12, padding: '8px 0', flexWrap: 'wrap' }}>
      {agents.map((a) => (
        <div key={a.id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: STATUS_COLORS[a.status] || STATUS_COLORS.unknown, display: 'inline-block' }} />
          <span style={{ color: '#94a3b8' }}>{a.name}</span>
        </div>
      ))}
    </div>
  );
}

export function ProgressSidepane() {
  useDashboardWS();
  const connected = useDashboardStore((s) => s.connected);
  const agents = useDashboardStore((s) => s.agents);

  return (
    <div style={{ display: 'flex', gap: 12, height: '100%', padding: 12 }}>
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 8, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>Agents</span>
          <span style={{ fontSize: 10, color: connected ? '#34d399' : '#f87171' }}>
            {connected ? '● live' : '○ disconnected'}
          </span>
        </div>
        <AgentList />
        <div style={{ flex: 1, borderRadius: 8, overflow: 'hidden', minHeight: 0 }}>
          <AgentGraph />
        </div>
      </div>
      <div
        style={{
          width: 320,
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
        }}
      >
        <div style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>
          Tasks
          <span style={{ fontSize: 10, color: 'rgba(148,163,184,0.6)', marginLeft: 8, fontWeight: 400 }}>
            pending / running / done
          </span>
        </div>
        <div
          style={{
            flex: 1,
            borderRadius: 8,
            background: 'rgba(26,26,46,0.5)',
            padding: 8,
            overflow: 'hidden',
            display: 'flex',
            flexDirection: 'column',
          }}
        >
          <KanbanStrip />
        </div>
      </div>
    </div>
  );
}
