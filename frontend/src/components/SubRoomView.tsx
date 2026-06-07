/**
 * SubRoomView — joins a liaison sub-room as a passive listener when expanded.
 *
 * On mount: fetches a token for the sub-room, connects a separate Room instance,
 * subscribes to audio (replacing main room audio), and shows the transcript.
 * On unmount (tile collapse): disconnects from the sub-room, main audio resumes.
 */

import { useState, useEffect, useRef } from "react";
import {
  Room,
  RoomEvent,
  RemoteTrack,
  Track,
  RoomOptions,
} from "livekit-client";

const BACKEND = import.meta.env.VITE_BACKEND_URL ?? "http://localhost:7880";

interface TranscriptLine {
  id: string;
  speaker: string;
  text: string;
  final: boolean;
}

interface Props {
  subRoom: string;
  workerId: string;
}

export function SubRoomView({ subRoom }: Props) {
  const [lines, setLines] = useState<TranscriptLine[]>([]);
  const [status, setStatus] = useState<"connecting" | "connected" | "error">("connecting");
  const roomRef = useRef<Room | null>(null);

  useEffect(() => {
    let cancelled = false;
    const room = new Room({ adaptiveStream: false } as RoomOptions);
    roomRef.current = room;

    async function connect() {
      try {
        const identity = `dashboard-sub-${Math.random().toString(36).slice(2, 8)}`;
        const res = await fetch(
          `${BACKEND}/token?room=${encodeURIComponent(subRoom)}&identity=${identity}`,
        );
        if (!res.ok) throw new Error(`token fetch failed: ${res.status}`);
        const { token, url } = await res.json();

        if (cancelled) return;

        await room.connect(url, token, { autoSubscribe: true });

        if (cancelled) {
          await room.disconnect();
          return;
        }

        setStatus("connected");

        // Play audio from sub-room tracks (replaces main room audio automatically
        // because only one AudioContext output is active per browser tab).
        room.on(RoomEvent.TrackSubscribed, (track: RemoteTrack) => {
          if (track.kind === Track.Kind.Audio) {
            track.attach(); // attaches to a new <audio> element and plays
          }
        });

        // Collect transcription segments from data messages.
        room.on(
          RoomEvent.DataReceived,
          (payload: Uint8Array, participant: any, _kind: any, topic?: string) => {
            if (topic !== "lk.transcription") return;
            try {
              const msg = JSON.parse(new TextDecoder().decode(payload));
              const segs: any[] = msg.segments ?? [];
              const speaker: string =
                participant?.name || participant?.identity || "agent";
              setLines((prev) => {
                const next = [...prev];
                for (const seg of segs) {
                  const idx = next.findIndex((l) => l.id === seg.id);
                  const line: TranscriptLine = {
                    id: seg.id,
                    speaker,
                    text: seg.text,
                    final: seg.final,
                  };
                  if (idx >= 0) next[idx] = line;
                  else next.push(line);
                }
                return next;
              });
            } catch {
              // ignore
            }
          },
        );
      } catch (err) {
        console.error("SubRoomView connect error:", err);
        if (!cancelled) setStatus("error");
      }
    }

    connect();

    return () => {
      cancelled = true;
      room.disconnect();
      roomRef.current = null;
    };
  }, [subRoom]);

  if (status === "connecting") {
    return <p className="subroom-status">Connecting to sub-room…</p>;
  }
  if (status === "error") {
    return <p className="subroom-status error">Could not join sub-room.</p>;
  }

  return (
    <div className="subroom-transcript">
      <p className="subroom-label">Live: {subRoom}</p>
      <div className="transcript-list">
        {lines.length === 0 && (
          <p className="transcript-empty">Waiting for conversation…</p>
        )}
        {lines.map((line) => (
          <div
            key={line.id}
            className={`transcript-line ${line.final ? "final" : "interim"}`}
          >
            <span className="tx-speaker">{line.speaker}</span>
            <span className="tx-text">{line.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
