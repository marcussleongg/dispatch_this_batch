export type WorkerStatus = "idle" | "spawning" | "active" | "done" | "error";

export interface WorkerState {
  label: string;
  status: WorkerStatus;
  subRoom: string | null;
  parentId: string | null;
  finding: string | null;  // latest finding snippet (first 120 chars)
}

export interface FindingEntry {
  source: string;
  sourceLabel: string;
  summary: string;
  ts: number;
}

export interface DashboardState {
  incidentId: string | null;
  facts: Record<string, string>;
  lastPacketAt: number | null;
  workers: Record<string, WorkerState>;
  findings: FindingEntry[];
}

// ── Packet types (JSON over LiveKit data_received, topic="dashboard") ─────────

interface BasePacket {
  ts: number;
  incident_id: string;
}

export interface SnapshotPacket extends BasePacket {
  type: "snapshot";
  facts: Record<string, string>;
  workers: Record<string, {
    label: string;
    status: WorkerStatus;
    sub_room: string | null;
    parent_id?: string | null;
  }>;
  findings: Array<{
    source: string;
    source_label: string;
    summary: string;
    ts: number;
  }>;
}

export interface FactUpdatePacket extends BasePacket {
  type: "fact_update";
  key: string;
  value: string;
}

export interface WorkerStatusPacket extends BasePacket {
  type: "worker_status";
  worker_id: string;
  label: string;
  status: WorkerStatus;
  sub_room: string | null;
  parent_id: string | null;
}

export interface FindingPacket extends BasePacket {
  type: "finding";
  source: string;
  source_label: string;
  summary: string;
}

export type DashboardPacket =
  | SnapshotPacket
  | FactUpdatePacket
  | WorkerStatusPacket
  | FindingPacket;
