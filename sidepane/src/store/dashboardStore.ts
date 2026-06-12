import { create } from 'zustand';
import type { AgentNode, TaskFlow, Edge, DashboardState } from '../types/dashboard';

interface DashboardStore extends DashboardState {
  hydrate: (state: DashboardState) => void;
  addAgent: (agent: AgentNode) => void;
  updateAgent: (id: string, partial: Partial<AgentNode>) => void;
  addTask: (task: TaskFlow) => void;
  updateTask: (id: string, partial: Partial<TaskFlow>) => void;
  addEdge: (edge: Edge) => void;
  setConnected: (connected: boolean) => void;
}

export const useDashboardStore = create<DashboardStore>((set) => ({
  agents: [],
  tasks: [],
  edges: [],
  lastUpdated: 0,
  connected: false,

  hydrate: (state) =>
    set({ ...state, lastUpdated: Date.now() }),

  addAgent: (agent) =>
    set((s) => ({
      agents: s.agents.some((a) => a.id === agent.id)
        ? s.agents.map((a) => (a.id === agent.id ? agent : a))
        : [...s.agents, agent],
      lastUpdated: Date.now(),
    })),

  updateAgent: (id, partial) =>
    set((s) => ({
      agents: s.agents.map((a) => (a.id === id ? { ...a, ...partial } : a)),
      lastUpdated: Date.now(),
    })),

  addTask: (task) =>
    set((s) => ({
      tasks: [...s.tasks, task],
      lastUpdated: Date.now(),
    })),

  updateTask: (id, partial) =>
    set((s) => ({
      tasks: s.tasks.map((t) => (t.id === id ? { ...t, ...partial } : t)),
      lastUpdated: Date.now(),
    })),

  addEdge: (edge) =>
    set((s) => ({
      edges: [...s.edges, edge],
      lastUpdated: Date.now(),
    })),

  setConnected: (connected) => set({ connected }),
}));
