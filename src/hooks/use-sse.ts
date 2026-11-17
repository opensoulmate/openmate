"use client";
import { useState, useEffect, useRef, useCallback } from "react";
import { getApiBaseUrl } from "@/lib/api-client";

export interface SSEEvent {
  id: string;
  organ: string;
  emoji: string;
  type: string;
  summary: string;
  detail?: Record<string, unknown>;
  timestamp?: number;
  collected_at?: number;
  [key: string]: unknown;
}

interface UseSSEOptions {
  /** Filter events by organ name */
  organ?: string | null;
  /** Max events to keep in memory (default: 100) */
  maxEvents?: number;
  /** Auto-connect on mount (default: true) */
  enabled?: boolean;
  /** Callback for each new event */
  onEvent?: (event: SSEEvent) => void;
}

interface UseSSEReturn {
  /** Current list of events (newest first) */
  events: SSEEvent[];
  /** Whether SSE is connected */
  connected: boolean;
  /** Connection error, if any */
  error: string | null;
  /** Manually reconnect */
  reconnect: () => void;
  /** Clear all events */
  clear: () => void;
}

/**
 * Reusable SSE hook for real-time event streaming from OpenSoul.
 *
 * Usage:
 *   const { events, connected } = useSSE({ organ: "vein" });
 *   const { events, connected } = useSSE(); // all organs
 */
export function useSSE(options: UseSSEOptions = {}): UseSSEReturn {
  const {
    organ = null,
    maxEvents = 100,
    enabled = true,
    onEvent,
  } = options;

  const [events, setEvents] = useState<SSEEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const connect = useCallback(() => {
    if (!enabled) return;
    if (esRef.current) {
      esRef.current.close();
    }

    const apiBase = getApiBaseUrl();
    const url = organ
      ? `${apiBase}/api/events/sse?organ=${encodeURIComponent(organ)}`
      : `${apiBase}/api/events/sse`;

    const es = new EventSource(url);
    esRef.current = es;
    setError(null);

    es.addEventListener("connected", () => {
      setConnected(true);
      setError(null);
    });

    es.addEventListener("message", (e) => {
      try {
        const event: SSEEvent = JSON.parse(e.data);
        setEvents((prev) => {
          if (prev.some((ev) => ev.id === event.id)) return prev;
          return [event, ...prev].slice(0, maxEvents);
        });
        onEventRef.current?.(event);
      } catch {}
    });

    es.onerror = () => {
      setConnected(false);
      setError("连接中断——正在重连...");
      // EventSource auto-reconnects
    };
  }, [enabled, organ, maxEvents]);

  const disconnect = useCallback(() => {
    if (esRef.current) {
      esRef.current.close();
      esRef.current = null;
      setConnected(false);
    }
  }, []);

  const clear = useCallback(() => {
    setEvents([]);
  }, []);

  useEffect(() => {
    connect();
    return disconnect;
  }, [connect, disconnect]);

  return { events, connected, error, reconnect: connect, clear };
}
