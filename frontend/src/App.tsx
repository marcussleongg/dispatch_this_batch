import { useState, useEffect } from "react";
import { LiveKitRoom } from "@livekit/components-react";
import "@livekit/components-styles";
import { Dashboard } from "./Dashboard";
import "./styles/sandbox.css";

const BACKEND = import.meta.env.VITE_BACKEND_URL ?? "http://localhost:7880";

interface RoomCreds {
  token: string;
  url: string;
  identity: string;
}

export default function App() {
  const [roomName, setRoomName] = useState<string>("");
  const [inputRoom, setInputRoom] = useState<string>("");
  const [creds, setCreds] = useState<RoomCreds | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Auto-discover active rooms on load.
  useEffect(() => {
    fetch(`${BACKEND}/rooms`)
      .then((r) => r.json())
      .then((data) => {
        if (data.rooms?.length > 0) setRoomName(data.rooms[0]);
      })
      .catch(() => {/* no-op — user can type the room name */});
  }, []);

  // Fetch token whenever we have a room name.
  useEffect(() => {
    if (!roomName) return;
    const identity = `dashboard-${Math.random().toString(36).slice(2, 10)}`;
    setCreds(null);
    setError(null);
    fetch(`${BACKEND}/token?room=${encodeURIComponent(roomName)}&identity=${identity}`)
      .then((r) => r.json())
      .then((data) => {
        if (data.token) setCreds(data);
        else setError("Token error: " + JSON.stringify(data));
      })
      .catch((e) => setError(String(e)));
  }, [roomName]);

  if (!creds) {
    return (
      <div className="app-loading">
        <h1>Dispatch Command Center</h1>
        {error && <p className="app-error">{error}</p>}
        <p>
          {roomName
            ? `Connecting to room: ${roomName}…`
            : "No active room found. Enter a room name:"}
        </p>
        <div className="room-input-row">
          <input
            className="room-input"
            placeholder="Room name"
            value={inputRoom}
            onChange={(e) => setInputRoom(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && setRoomName(inputRoom)}
          />
          <button className="room-btn" onClick={() => setRoomName(inputRoom)}>
            Connect
          </button>
        </div>
      </div>
    );
  }

  return (
    <LiveKitRoom
      serverUrl={creds.url}
      token={creds.token}
      audio={false}
      video={false}
      connect={true}
    >
      <Dashboard />
    </LiveKitRoom>
  );
}
