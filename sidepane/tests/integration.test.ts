import { describe, it, expect } from 'vitest';
import type { DashboardState, WsEvent } from '../src/types/dashboard';
import { SEED_DASHBOARD_STATE } from '../src/fixtures/seedDashboardState';

describe('DashboardState model', () => {
  it('seed has 4 agents', () => {
    expect(SEED_DASHBOARD_STATE.agents).toHaveLength(4);
  });

  it('seed has 6 tasks', () => {
    expect(SEED_DASHBOARD_STATE.tasks).toHaveLength(6);
  });

  it('seed has 5 edges', () => {
    expect(SEED_DASHBOARD_STATE.edges).toHaveLength(5);
  });

  it('all tasks exist in valid status', () => {
    const valid = ['pending', 'assigned', 'running', 'completed', 'failed', 'verified'];
    for (const t of SEED_DASHBOARD_STATE.tasks) {
      expect(valid).toContain(t.status);
    }
  });
});

describe('WS event protocol', () => {
  it('full_state event parses correctly', () => {
    const event: WsEvent = { type: 'full_state', payload: SEED_DASHBOARD_STATE };
    expect(event.type).toBe('full_state');
    expect(event.payload.agents.length).toBe(4);
  });

  it('agent:status_changed event contains agent', () => {
    const event: WsEvent = {
      type: 'agent:status_changed',
      agent: SEED_DASHBOARD_STATE.agents[0],
    };
    expect(event.type).toBe('agent:status_changed');
    expect(event.agent.name).toBeDefined();
  });

  it('task:created event contains task', () => {
    const event: WsEvent = {
      type: 'task:created',
      task: SEED_DASHBOARD_STATE.tasks[0],
    };
    expect(event.type).toBe('task:created');
    expect(event.task.title).toBeDefined();
  });

  it('task:status_changed event has id and status', () => {
    const event: WsEvent = {
      type: 'task:status_changed',
      taskId: 't1',
      status: 'completed',
    };
    expect(event.type).toBe('task:status_changed');
    expect(event.taskId).toBe('t1');
    expect(event.status).toBe('completed');
  });

  it('edge:added event contains edge', () => {
    const event: WsEvent = {
      type: 'edge:added',
      edge: { source: 'A', target: 'B' },
    };
    expect(event.type).toBe('edge:added');
    expect(event.edge.source).toBe('A');
  });

  it('heartbeat event has no payload', () => {
    const event: WsEvent = { type: 'heartbeat' };
    expect(event.type).toBe('heartbeat');
  });
});
