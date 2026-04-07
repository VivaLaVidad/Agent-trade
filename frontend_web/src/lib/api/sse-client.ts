import type { StreamEvent } from "./types";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8900";

export async function* streamTradeEvents(
  sessionId: string,
  signal?: AbortSignal,
): AsyncGenerator<StreamEvent> {
  const url = `${API_BASE}/api/v1/trade/stream?session_id=${encodeURIComponent(sessionId)}`;

  const response = await fetch(url, {
    headers: { Accept: "text/event-stream" },
    signal,
  });

  if (!response.ok) {
    throw new Error(`SSE connection failed: ${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";

      for (const line of lines) {
        if (line.startsWith("data: ")) {
          const raw = line.slice(6).trim();
          if (raw === "[DONE]") return;
          try {
            yield JSON.parse(raw) as StreamEvent;
          } catch {
            // skip malformed JSON
          }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}
