import { useDashboardEvents } from "./hooks/useDashboardEvents";
import { SandboxCanvas } from "./components/SandboxCanvas";

export function Dashboard() {
  const state = useDashboardEvents();
  return <SandboxCanvas state={state} />;
}
