"""In-memory pub/sub hub that pushes live events to connected devices over
Server-Sent Events (SSE). Subscribers are grouped by role so a new order can be
relayed instantly to the kitchen and admin phones."""
import json
import queue
import threading

_lock = threading.Lock()
_subscribers = {}  # client_id -> {"role": str, "q": Queue}
_counter = 0


def subscribe(role: str):
    global _counter
    with _lock:
        _counter += 1
        cid = _counter
        q = queue.Queue(maxsize=100)
        _subscribers[cid] = {"role": role, "q": q}
    return cid, q


def unsubscribe(cid: int):
    with _lock:
        _subscribers.pop(cid, None)


def publish(roles, event: str, data: dict):
    """Send an event to every subscriber whose role is in `roles`."""
    payload = json.dumps({"event": event, "data": data})
    with _lock:
        targets = [s["q"] for s in _subscribers.values() if s["role"] in roles]
    for q in targets:
        try:
            q.put_nowait(payload)
        except queue.Full:
            pass  # slow client; drop rather than block the whole app


def connection_count():
    with _lock:
        return len(_subscribers)
