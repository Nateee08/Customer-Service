"""Share one bounded background sheet refresh across portal requests."""
import threading
import time


class SheetSnapshot:
    def __init__(self, ttl=10, retry_delay=5):
        self.ttl = ttl
        self.retry_delay = retry_delay
        self._lock = threading.Lock()
        self._value = None
        self._updated = 0
        self._attempted = float('-inf')
        self._failed = False
        self._pending = None

    def get(self, loader, wait_seconds=2):
        start = False
        with self._lock:
            fresh = self._value is not None and not self._failed and time.monotonic() - self._updated < self.ttl
            if not fresh and self._pending is None and time.monotonic() - self._attempted >= self.retry_delay:
                self._pending = threading.Event()
                self._attempted = time.monotonic()
                start = True
            pending = self._pending
        if start:
            def refresh():
                try:
                    value = loader()
                    with self._lock:
                        self._value = value
                        self._updated = time.monotonic()
                        self._failed = False
                except Exception:
                    # Keep the last complete snapshot for display, never for deletion.
                    with self._lock:
                        self._failed = True
                finally:
                    with self._lock:
                        self._pending = None
                        pending.set()
            try:
                threading.Thread(target=refresh, name='portal-sheet-refresh', daemon=True).start()
            except RuntimeError:
                with self._lock:
                    self._failed = True
                    self._pending = None
                    pending.set()
        if pending:
            pending.wait(wait_seconds)
        with self._lock:
            fresh = self._value is not None and not self._failed and time.monotonic() - self._updated < self.ttl
            return self._value, fresh, self._pending is not None
