import { useEffect, useRef } from 'react';
import { useDashboardStore } from '../store/dashboardStore';
import type { WsEvent } from '../types/dashboard';

const WS_URL = 'ws://localhost:8080/dashboard';

export function useDashboardWS() {
  const wsRef = useRef<WebSocket | null>(null);
  const store = useDashboardStore();

  useEffect(() => {
    let reconnectTimer: ReturnType<typeof setTimeout>;

    function connect() {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        store.setConnected(true);
      };

      ws.onmessage = (event) => {
        try {
          const msg: WsEvent = JSON.parse(event.data);
          handleEvent(msg);
        } catch (e) {
          console.error('WS parse error', e);
        }
      };

      ws.onclose = () => {
        store.setConnected(false);
        reconnectTimer = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };
    }

    function handleEvent(msg: WsEvent) {
      switch (msg.type) {
        case 'full_state':
          store.hydrate(msg.payload);
          break;
        case 'agent:status_changed':
          store.addAgent(msg.agent);
          break;
        case 'task:created':
          store.addTask(msg.task);
          break;
        case 'task:status_changed':
          store.updateTask(msg.taskId, { status: msg.status });
          break;
        case 'edge:added':
          store.addEdge(msg.edge);
          break;
      }
    }

    connect();

    return () => {
      clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
  }, []);
}
