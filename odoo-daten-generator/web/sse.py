"""Per-run event broker feeding the SSE stream.

A run takes 2–5 minutes — past every default gateway timeout — so the API is
``202 + run_id`` and progress arrives over Server-Sent Events instead of being
the response to a long POST.

Events are produced by worker *threads* and consumed by *async* request handlers.
Rather than bridge with an asyncio queue per subscriber, each run keeps an
append-only event list and readers hold a cursor: the reader polls, the writer
never blocks, a client that connects late replays everything from the start, and
a dropped connection reconnects with ``Last-Event-ID`` and misses nothing.
"""
import threading
from typing import Any, Dict, List, Optional

# Keep a bounded history so a very chatty run cannot grow without limit. The
# frontend only renders a scrollback window anyway.
MAX_EVENTS = 5000


class RunEventStream:
    def __init__(self, run_id: str):
        self.run_id = run_id
        self._events: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._closed = False
        # Number of events dropped off the front, so cursors stay monotonic.
        self._offset = 0

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def publish(self, event_type: str, data: Any) -> None:
        with self._lock:
            if self._closed:
                return
            index = self._offset + len(self._events)
            self._events.append({"id": index, "type": event_type, "data": data})
            if len(self._events) > MAX_EVENTS:
                trimmed = len(self._events) - MAX_EVENTS
                del self._events[:trimmed]
                self._offset += trimmed

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def since(self, cursor: int) -> List[Dict[str, Any]]:
        """Events with id > cursor, oldest first."""
        with self._lock:
            start = max(0, cursor + 1 - self._offset)
            return list(self._events[start:])

    def latest_id(self) -> int:
        with self._lock:
            return self._offset + len(self._events) - 1


class EventBroker:
    def __init__(self):
        self._streams: Dict[str, RunEventStream] = {}
        self._lock = threading.Lock()

    def create(self, run_id: str) -> RunEventStream:
        with self._lock:
            stream = RunEventStream(run_id)
            self._streams[run_id] = stream
            return stream

    def get(self, run_id: str) -> Optional[RunEventStream]:
        with self._lock:
            return self._streams.get(run_id)

    def forget(self, run_id: str) -> None:
        with self._lock:
            self._streams.pop(run_id, None)
