import logging
import queue
import threading

logger = logging.getLogger("ai-factory")


class TaskQueue:
    """
    Simple in-process FIFO queue of task IDs awaiting processing.

    This is intentionally in-memory only for this step — it does not
    survive a server restart. Any task left NEW after a restart is still
    recoverable via Orchestrator.run_pending(), which scans the DB
    directly rather than relying on this queue.

    Thread-safe via Python's built-in queue.Queue, since Step 38's
    background worker will pull from this on a separate thread while
    the API pushes onto it from request-handling threads.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        # Singleton so every part of the app shares the same queue instance.
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._queue = queue.Queue()
        return cls._instance

    def enqueue(self, task_id: str) -> None:
        self._queue.put(task_id)
        logger.info(f"TaskQueue: enqueued task {task_id} (size now {self._queue.qsize()})")

    def dequeue(self, block: bool = False, timeout: float = None):
        """
        Pop the next task ID off the queue. Returns None if empty and
        block=False. If block=True, waits up to `timeout` seconds (or
        forever if timeout=None) for an item to become available.
        """
        try:
            task_id = self._queue.get(block=block, timeout=timeout)
            logger.info(f"TaskQueue: dequeued task {task_id} (size now {self._queue.qsize()})")
            return task_id
        except queue.Empty:
            return None

    def size(self) -> int:
        return self._queue.qsize()

    def is_empty(self) -> bool:
        return self._queue.empty()