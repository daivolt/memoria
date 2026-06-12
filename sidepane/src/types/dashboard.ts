export type AgentStatus = 'active' | 'idle' | 'busy' | 'stale' | 'unknown';
export type TaskStatus = 'pending' | 'assigned' | 'running' | 'completed' | 'failed' | 'verified';

export interface AgentNode {
  id: string;
  name: string;
  status: AgentStatus;
  currentTask?: string;
  lastHeartbeat: number;
  x?: number;
  y?: number;
}

export interface TaskFlow {
  id: string;
  title: string;
  status: TaskStatus;
  assignedTo?: string;
  createdAt: number;
  completedAt?: number;
}

export interface Edge {
  source: string;
  target: string;
  label?: string;
}

export interface DashboardState {
  agents: AgentNode[];
  tasks: TaskFlow[];
  edges: Edge[];
  lastUpdated: number;
  connected: boolean;
}

export type WsEvent =
  | { type: 'full_state'; payload: DashboardState }
  | { type: 'agent:status_changed'; agent: AgentNode }
  | { type: 'task:created'; task: TaskFlow }
  | { type: 'task:status_changed'; taskId: string; status: TaskStatus }
  | { type: 'edge:added'; edge: Edge }
  | { type: 'heartbeat' };
