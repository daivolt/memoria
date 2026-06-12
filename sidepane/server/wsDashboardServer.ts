import { WebSocketServer, WebSocket } from 'ws';
import type { DashboardState, WsEvent } from '../src/types/dashboard';

const PORT = parseInt(process.env.WS_PORT || '8080', 10);
const wss = new WebSocketServer({ port: PORT });

let state: DashboardState = {
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
  connected: true,
};

function broadcast(msg: WsEvent) {
  const data = JSON.stringify(msg);
  wss.clients.forEach((client) => {
    if (client.readyState === WebSocket.OPEN) {
      client.send(data);
    }
  });
}

wss.on('connection', (ws) => {
  // Send full state on connect
  const init: WsEvent = { type: 'full_state', payload: state };
  ws.send(JSON.stringify(init));

  ws.on('message', (raw) => {
    try {
      const msg = JSON.parse(raw.toString());
      if (msg.type === 'hydrate') {
        state = { ...state, ...msg.payload, lastUpdated: Date.now() };
        broadcast({ type: 'full_state', payload: state });
      }
    } catch (e) {
      console.error('WS parse error', e);
    }
  });
});

// Demo: simulate agent activity every 8s
setInterval(() => {
  const demoEvents: (() => WsEvent)[] = [
    () => ({
      type: 'agent:status_changed' as const,
      agent: { ...state.agents[0], status: Math.random() > 0.5 ? 'active' : 'busy' as any, lastHeartbeat: Date.now() },
    }),
    () => ({
      type: 'task:status_changed' as const,
      taskId: 't4',
      status: Math.random() > 0.5 ? 'assigned' : 'running',
    }),
    () => ({
      type: 'edge:added' as const,
      edge: { source: state.agents[Math.floor(Math.random() * state.agents.length)].name, target: state.agents[Math.floor(Math.random() * state.agents.length)].name },
    }),
  ];
  const pick = demoEvents[Math.floor(Math.random() * demoEvents.length)];
  const event = pick();
  // Update local state
  if (event.type === 'agent:status_changed') {
    const idx = state.agents.findIndex((a) => a.id === event.agent.id);
    if (idx >= 0) state.agents[idx] = event.agent;
  } else if (event.type === 'task:status_changed') {
    const t = state.tasks.find((t) => t.id === event.taskId);
    if (t) t.status = event.status;
  } else if (event.type === 'edge:added') {
    state.edges.push(event.edge);
  }
  state.lastUpdated = Date.now();
  broadcast(event);
}, 8000);

console.log(`Dashboard WS server listening on ws://localhost:${PORT}/dashboard`);
