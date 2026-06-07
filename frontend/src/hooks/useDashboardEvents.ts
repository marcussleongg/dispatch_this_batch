import { useReducer, useCallback } from "react";
import { useDataChannel } from "@livekit/components-react";
import type {
  DashboardPacket,
  DashboardState,
  FindingEntry,
  WorkerState,
} from "../types";

const STATIC_WORKERS: Record<string, WorkerState> = {
  orchestrator: { label: "Call-Taker", status: "active", subRoom: null, parentId: null, finding: null },
  supervisor:   { label: "Supervisor",  status: "idle",   subRoom: null, parentId: "orchestrator", finding: null },
};

export const initialState: DashboardState = {
  incidentId: null,
  facts: {},
  lastPacketAt: null,
  workers: { ...STATIC_WORKERS },
  findings: [],
};

const KNOWN_TYPES = new Set(["snapshot", "fact_update", "worker_status", "finding"]);

function reducer(state: DashboardState, packet: DashboardPacket): DashboardState {
  const now = Date.now();
  switch (packet.type) {
    case "snapshot": {
      const workers: Record<string, WorkerState> = { ...STATIC_WORKERS };
      for (const [wid, w] of Object.entries(packet.workers)) {
        workers[wid] = {
          label: w.label,
          status: w.status,
          subRoom: w.sub_room ?? null,
          parentId: w.parent_id ?? null,
          finding: null,
        };
      }
      for (const f of packet.findings) {
        const wid = sourceToWorkerId(f.source);
        if (workers[wid]) {
          workers[wid] = { ...workers[wid], finding: f.summary.slice(0, 120) };
        }
      }
      const findings: FindingEntry[] = packet.findings.map((f) => ({
        source: f.source,
        sourceLabel: f.source_label,
        summary: f.summary,
        ts: f.ts,
      }));
      return { incidentId: packet.incident_id, facts: packet.facts, lastPacketAt: now, workers, findings };
    }

    case "fact_update":
      return {
        ...state,
        lastPacketAt: now,
        incidentId: state.incidentId ?? packet.incident_id,
        facts: { ...state.facts, [packet.key]: packet.value },
        workers: state.workers.supervisor?.status === "idle"
          ? { ...state.workers, supervisor: { ...state.workers.supervisor, status: "active" } }
          : state.workers,
      };

    case "worker_status": {
      const existing = state.workers[packet.worker_id];
      return {
        ...state,
        lastPacketAt: now,
        workers: {
          ...state.workers,
          [packet.worker_id]: {
            label: packet.label,
            status: packet.status,
            subRoom: packet.sub_room ?? null,
            parentId: packet.parent_id ?? null,
            finding: existing?.finding ?? null,
          },
        },
      };
    }

    case "finding": {
      const wid = sourceToWorkerId(packet.source);
      const updatedWorkers = { ...state.workers };
      if (updatedWorkers[wid]) {
        updatedWorkers[wid] = { ...updatedWorkers[wid], finding: packet.summary.slice(0, 120) };
      }
      return {
        ...state,
        lastPacketAt: now,
        workers: updatedWorkers,
        findings: [...state.findings, { source: packet.source, sourceLabel: packet.source_label, summary: packet.summary, ts: packet.ts }],
      };
    }

    default:
      return state;
  }
}

function sourceToWorkerId(source: string): string {
  if (source.startsWith("agency_") || source.startsWith("sim_")) return source;
  return `agency_${source}`;
}

// Must be called inside a <LiveKitRoom> context tree.
export function useDashboardEvents(): DashboardState {
  const [state, dispatch] = useReducer(reducer, initialState);

  const onMessage = useCallback((msg: { payload: Uint8Array }) => {
    try {
      const packet = JSON.parse(new TextDecoder().decode(msg.payload)) as DashboardPacket;
      if (!KNOWN_TYPES.has(packet.type)) return;
      dispatch(packet);
    } catch {
      // ignore malformed packets
    }
  }, []);

  useDataChannel("dashboard", onMessage);

  return state;
}
