import React from 'react';
import { useDashboardStore } from '../store/dashboardStore';
import type { TaskFlow } from '../types/dashboard';

const STATUS_ORDER: Record<string, number> = {
  pending: 0,
  assigned: 1,
  running: 2,
  completed: 3,
  failed: 4,
  verified: 5,
};

const STATUS_COLORS: Record<string, string> = {
  pending: 'var(--warning)',
  assigned: 'var(--accent)',
  running: 'var(--accent)',
  completed: 'var(--success)',
  failed: 'var(--danger)',
  verified: 'var(--success)',
};

function TaskCard({ task }: { task: TaskFlow }) {
  const borderColor = STATUS_COLORS[task.status] || 'var(--border)';
  return (
    <div
      style={{
        background: 'rgba(26,26,46,0.85)',
        borderRadius: 6,
        padding: '8px 10px',
        fontSize: 12,
        borderLeft: `3px solid ${borderColor}`,
        opacity: task.status === 'completed' || task.status === 'verified' ? 0.7 : 1,
      }}
    >
      <div
        style={{
          fontWeight: 600,
          color: '#e2e8f0',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
      >
        {task.title.slice(0, 60)}
      </div>
      <div style={{ fontSize: 10, color: 'rgba(148,163,184,0.6)', marginTop: 2 }}>
        {task.status} · {task.assignedTo ? task.assignedTo.slice(0, 16) : 'unassigned'} · {new Date(task.createdAt).toLocaleTimeString()}
      </div>
    </div>
  );
}

export function KanbanStrip() {
  const tasks = useDashboardStore((s) => s.tasks);

  const sorted = [...tasks].sort(
    (a, b) => (STATUS_ORDER[a.status] ?? 9) - (STATUS_ORDER[b.status] ?? 9)
  );

  return (
    <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 4, padding: '4px 0' }}>
      {sorted.slice(0, 40).map((task) => (
        <TaskCard key={task.id} task={task} />
      ))}
      {sorted.length === 0 && (
        <div style={{ color: 'rgba(148,163,184,0.4)', textAlign: 'center', padding: 20, fontSize: 12 }}>
          No tasks yet — waiting for agent activity
        </div>
      )}
    </div>
  );
}
