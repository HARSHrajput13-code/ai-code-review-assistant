"""A bounded first-in, first-out queue."""

from collections import deque


class BoundedQueue:
    """Keeps at most `capacity` items, dropping the oldest when full."""

    def __init__(self, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self._items: deque[str] = deque(maxlen=capacity)

    def push(self, item: str) -> None:
        """Add an item to the back of the queue."""
        self._items.append(item)

    def pop(self) -> str:
        """Remove and return the oldest item."""
        if not self._items:
            raise IndexError("pop from an empty queue")
        return self._items.popleft()
