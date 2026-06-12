import type { DashboardState } from '../types/dashboard';

export const SEED_DASHBOARD_STATE: DashboardState = {
  agents: [
    { id: 'orchestrator', name: 'Orchestrator', status: 'active', currentTask: 'coordinating', lastHeartbeat: Date.now() },
    { id: 'researcher', name: 'Researcher', status: 'active', currentTask: 'design spec', lastHeartbeat: Date.now() },
    { id: 'builder', name: 'Builder', status: 'busy', currentTask: 'build sidepane', lastHeartbeat: Date.now() },
    { id: 'mini', name: 'Mini', status: 'idle', lastHeartbeat: Date.now() - 60000 },
  ],
  tasks: [
    { id: 't1', title: 'Design spec', status: 'completed', assignedTo: 'Researcher', createdAt: Date.now() - 3600000, completedAt: Date.now() - 1800000 },
    { id: 't2', title: 'Build React scaffold', status: 'running', assignedTo: 'Builder', createdAt: Date.now() - 1800000 },
    { id: 't3', title: 'Merge converged build', status: 'completed', assignedTo: 'Mini', createdAt: Date.now() - 900000, completedAt: Date.now() - 300000 },
    { id: 't4', title: 'Validation & QA', status: 'pending', createdAt: Date.now() - 600000 },
    { id: 't5', title: 'WS server endpoint', status: 'pending', assignedTo: 'Builder', createdAt: Date.now() - 300000 },
    { id: 't6', title: 'Smoke test live WS', status: 'pending', assignedTo: 'Mini', createdAt: Date.now() - 120000 },
  ],
  edges: [
    { source: 'Orchestrator', target: 'Researcher', label: 'delegates' },
    { source: 'Orchestrator', target: 'Builder', label: 'assigns' },
    { source: 'Orchestrator', target: 'Mini', label: 'assigns' },
    { source: 'Researcher', target: 'Builder', label: 'informs' },
    { source: 'Mini', target: 'Builder', label: 'reviews' },
  ],
  lastUpdated: Date.now(),
  connected: false,
};
