import type { DashboardState } from "../types";
import { AgentNode } from "./AgentNode";

interface Props {
  state: DashboardState;
}

/**
 * Renders the agent tree as indented rows — no connecting lines.
 * Tree shape: orchestrator → supervisor → (web_research, agency_*) → (sim_*)
 */
export function SandboxCanvas({ state }: Props) {
  const { workers, facts, incidentId, lastPacketAt } = state;

  // Collect agency liaisons and their sims.
  const agencyIds = Object.keys(workers).filter((id) => id.startsWith("agency_"));
  const simFor = (agencyId: string) => {
    const simId = `sim_${agencyId.replace("agency_", "")}`;
    return workers[simId] ? simId : null;
  };

  const webResearch = workers["web_research"];

  return (
    <div className="sandbox-canvas">
      {/* Header bar */}
      <div className="canvas-header">
        <span className="incident-chip">
          {incidentId ? `INC-${incidentId.toUpperCase()}` : "IDLE"}
        </span>
        <span className={`status-badge ${incidentId ? "live" : "idle"}`}>
          {incidentId ? "● LIVE" : "○ IDLE"}
        </span>
        <span className="last-packet-debug">
          {lastPacketAt
            ? `last pkt ${((Date.now() - lastPacketAt) / 1000).toFixed(1)}s ago`
            : "no packets yet"}
        </span>
        <div className="facts-bar">
          {(["location", "un_number", "chemical", "injuries"] as const).map((k) =>
            facts[k] ? (
              <span key={k} className="fact-chip">
                <span className="fact-chip-key">{k === "un_number" ? "UN#" : k}</span>
                {facts[k]}
              </span>
            ) : null,
          )}
        </div>
      </div>

      {/* Tree root: Call-Taker */}
      <div className="tree-root">
        <AgentNode workerId="orchestrator" worker={workers.orchestrator}>

          {/* Level 2: Supervisor */}
          {workers.supervisor && (
            <AgentNode
              workerId="supervisor"
              worker={workers.supervisor}
              facts={facts}
            >
              {/* Level 3: Web Research */}
              {webResearch && (
                <AgentNode workerId="web_research" worker={webResearch} />
              )}

              {/* Level 3: Agency Liaisons + their sims */}
              {agencyIds.map((agencyId) => {
                const simId = simFor(agencyId);
                return (
                  <AgentNode
                    key={agencyId}
                    workerId={agencyId}
                    worker={workers[agencyId]}
                  >
                    {simId && (
                      <AgentNode
                        workerId={simId}
                        worker={workers[simId]}
                      />
                    )}
                  </AgentNode>
                );
              })}
            </AgentNode>
          )}
        </AgentNode>
      </div>
    </div>
  );
}
