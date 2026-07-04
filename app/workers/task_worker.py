import logging
import threading

from app.services.task_queue import TaskQueue
from app.services.task_processor import TaskProcessor

logger = logging.getLogger("ai-factory")


class TaskWorker:
    """
    Background thread that continuously pulls task IDs off TaskQueue
    and runs them through TaskProcessor. One task is processed at a
    time (single worker thread) — this step is about establishing
    automatic processing, not concurrency/parallelism.

    Started/stopped via app.main's startup/shutdown events.
    """

    def __init__(self, poll_timeout: float = 1.0):
        self.queue = TaskQueue()
        self.processor = TaskProcessor()
        self.poll_timeout = poll_timeout
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        if self._thread and self._thread.is_alive():
            logger.warning("TaskWorker: start() called but worker already running")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="TaskWorker")
        self._thread.start()
        logger.info("TaskWorker: started")

    def stop(self):
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("TaskWorker: stopped")

    def _run_loop(self):
        while not self._stop_event.is_set():
            task_id = self.queue.dequeue(block=True, timeout=self.poll_timeout)

            if task_id is None:
                # Nothing in the queue within the timeout window; loop
                # back and check _stop_event again rather than blocking
                # forever, so shutdown stays responsive.
                continue

            logger.info(f"TaskWorker: picked up task {task_id}")
            try:
                self.processor.process(task_id)
            except Exception as e:
                # process() already marks the task FAILED and logs
                # internally; just prevent one bad task from killing
                # the worker thread itself.
                logger.error(f"TaskWorker: task {task_id} raised during processing: {e}")