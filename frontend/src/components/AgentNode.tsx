import { useState, useEffect, useRef } from "react";
import { useTracks, useTrackTranscription } from "@livekit/components-react";
import { Track } from "livekit-client";
import type { WorkerState } from "../types";
import { SubRoomView } from "./SubRoomView";

const STATUS_COLORS: Record<string, string> = {
  idle:     "#4b5563",
  spawning: "#f59e0b",
  active:   "#22c55e",
  done:     "#3b82f6",
  error:    "#ef4444",
};

const FACT_LABELS: Record<string, string> = {
  location:    "Location",
  un_number:   "UN #",
  chemical:    "Chemical",
  injuries:    "Injuries",
  caller_safe: "Caller Safe",
  situation:   "Situation",
};

interface Props {
  workerId: string;
  worker: WorkerState;
  facts?: Record<string, string>;
  children?: React.ReactNode;
}

export function AgentNode({ workerId, worker, facts, children }: Props) {
  const [expanded, setExpanded] = useState(false);
  const [visible, setVisible] = useState(false);
  const prevStatus = useRef(worker.status);

  // Animate in on mount (delayed by one frame so CSS transition fires).
  useEffect(() => {
    const t = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(t);
  }, []);

  // Re-animate when transitioning from idle to spawning.
  useEffect(() => {
    if (prevStatus.current === "idle" && worker.status === "spawning") {
      setVisible(false);
      requestAnimationFrame(() => setVisible(true));
    }
    prevStatus.current = worker.status;
  }, [worker.status]);

  const color = STATUS_COLORS[worker.status] ?? "#4b5563";
  const isClickable =
    worker.subRoom !== null ||
    workerId === "orchestrator" ||
    workerId === "supervisor";

  return (
    <div className={`agent-node-wrapper ${visible ? "visible" : ""}`}>
      <div
        className={`agent-node status-${worker.status} ${expanded ? "expanded" : ""}`}
        onClick={() => isClickable && setExpanded((e) => !e)}
        style={{ cursor: isClickable ? "pointer" : "default" }}
      >
        <div className="node-header">
          <span
            className={`status-dot ${worker.status === "active" || worker.status === "spawning" ? "pulse" : ""}`}
            style={{ background: color }}
          />
          <span className="node-label">{worker.label}</span>
          {worker.subRoom && (
            <span className="node-subroom">{worker.subRoom}</span>
          )}
          {isClickable && (
            <span className="expand-hint">{expanded ? "▲" : "▼"}</span>
          )}
        </div>

        {!expanded && worker.finding && (
          <div className="node-snippet">
            {worker.finding}
            {worker.finding.length >= 120 ? "…" : ""}
          </div>
        )}

        {expanded && (
          <div className="node-detail">
            {workerId === "supervisor" && facts && Object.keys(facts).length > 0 && (
              <div className="node-facts">
                {Object.entries(facts).map(([k, v]) => (
                  <div key={k} className="fact-row">
                    <span className="fact-key">{FACT_LABELS[k] ?? k}</span>
                    <span className="fact-val">{v}</span>
                  </div>
                ))}
              </div>
            )}

            {workerId === "orchestrator" && <MainRoomTranscript />}

            {worker.subRoom && (
              <SubRoomView subRoom={worker.subRoom} workerId={workerId} />
            )}

            {!worker.subRoom &&
              workerId !== "orchestrator" &&
              workerId !== "supervisor" &&
              worker.finding && (
                <p className="node-full-finding">{worker.finding}</p>
              )}
          </div>
        )}
      </div>

      {children && <div className="node-children">{children}</div>}
    </div>
  );
}

// ── Main room transcript (orchestrator tile) ──────────────────────────────────

function MainRoomTranscript() {
  const tracks = useTracks([Track.Source.Microphone]);

  if (tracks.length === 0) {
    return <p className="transcript-empty">No audio tracks yet…</p>;
  }

  return (
    <div className="transcript-list">
      {tracks.map((ref) => (
        <TrackLines key={ref.publication?.trackSid} trackRef={ref} />
      ))}
    </div>
  );
}

function TrackLines({ trackRef }: { trackRef: NonNullable<Parameters<typeof useTrackTranscription>[0]> }) {
  const { segments } = useTrackTranscription(trackRef);
  const participant = trackRef.participant;
  const name = participant?.name || participant?.identity || "unknown";

  return (
    <>
      {segments.map((seg) => (
        <div key={seg.id} className={`transcript-line ${seg.final ? "final" : "interim"}`}>
          <span className="tx-speaker">{name}</span>
          <span className="tx-text">{seg.text}</span>
        </div>
      ))}
    </>
  );
}
