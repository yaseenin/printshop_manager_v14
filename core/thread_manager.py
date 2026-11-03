# core/thread_manager.py — Unified background thread management

import threading
import time
from typing import Callable, Optional, Dict
from datetime import datetime, timedelta
import config
from core.logging_setup import setup_logging

logger = setup_logging(__name__)


class BackgroundWorker:
    """
    Base class for long-running background threads.
    Provides clean shutdown, error handling, and health monitoring.
    """
    
    def __init__(self, name: str, interval_seconds: float):
        """
        Args:
            name: Worker name (for logging and identification)
            interval_seconds: How often to run the task (0 = continuous)
        """
        self.name = name
        self.interval = interval_seconds
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.last_run: Optional[datetime] = None
        self.last_error: Optional[str] = None
        self.run_count = 0
        self.error_count = 0
        self.is_running = False
    
    def start(self) -> None:
        """Start the background worker thread."""
        if self.thread is not None and self.thread.is_alive():
            logger.warning(f"Worker '{self.name}' is already running")
            return
        
        self.stop_event.clear()
        self.thread = threading.Thread(target=self._run_loop, daemon=True, name=self.name)
        self.thread.start()
        logger.info(f"Worker '{self.name}' started")
    
    def stop(self, timeout: float = config.THREAD_SHUTDOWN_TIMEOUT) -> bool:
        """
        Gracefully stop the worker.
        
        Args:
            timeout: Max seconds to wait for shutdown
        
        Returns:
            True if stopped cleanly, False if timeout
        """
        if self.thread is None or not self.thread.is_alive():
            return True
        
        logger.info(f"Stopping worker '{self.name}'...")
        self.stop_event.set()
        self.thread.join(timeout=timeout)
        
        if self.thread.is_alive():
            logger.error(f"Worker '{self.name}' did not stop within {timeout}s")
            return False
        
        logger.info(f"Worker '{self.name}' stopped")
        return True
    
    def _run_loop(self) -> None:
        """Main execution loop (runs in background thread)."""
        self.is_running = True
        logger.info(f"Worker '{self.name}' loop started")
        
        try:
            if self.interval > 0:
                # Interval-based execution
                while not self.stop_event.is_set():
                    try:
                        self.run()
                        self.last_run = datetime.now()
                        self.run_count += 1
                    except Exception as e:
                        self.last_error = str(e)
                        self.error_count += 1
                        logger.exception(f"Error in worker '{self.name}'")
                    
                    self.stop_event.wait(timeout=self.interval)
            else:
                # Continuous execution
                while not self.stop_event.is_set():
                    try:
                        self.run()
                        self.last_run = datetime.now()
                        self.run_count += 1
                    except Exception as e:
                        self.last_error = str(e)
                        self.error_count += 1
                        logger.exception(f"Error in worker '{self.name}'")
                        time.sleep(1)  # Longer sleep on errors to prevent CPU thrashing
        finally:
            self.is_running = False
            logger.info(f"Worker '{self.name}' loop ended")
    
    def run(self) -> None:
        """Override this method with your task logic."""
        raise NotImplementedError
    
    def get_status(self) -> Dict:
        """Get worker health status."""
        return {
            "name": self.name,
            "running": self.is_running,
            "thread_alive": self.thread.is_alive() if self.thread else False,
            "last_run": self.last_run.isoformat() if self.last_run else None,
            "run_count": self.run_count,
            "error_count": self.error_count,
            "last_error": self.last_error
        }


class ThreadManager:
    """
    Central manager for all background workers.
    Ensures clean startup/shutdown and health monitoring.
    """
    
    def __init__(self):
        self.workers: Dict[str, BackgroundWorker] = {}
        self.lock = threading.Lock()
        logger.info("ThreadManager initialized")
    
    def register(self, worker: BackgroundWorker) -> None:
        """Register a new background worker."""
        with self.lock:
            if worker.name in self.workers:
                logger.warning(f"Worker '{worker.name}' already registered, replacing")
            self.workers[worker.name] = worker
            logger.info(f"Worker '{worker.name}' registered")
    
    def start_all(self) -> None:
        """Start all registered workers."""
        with self.lock:
            for worker in self.workers.values():
                worker.start()
        logger.info("All workers started")
    
    def stop_all(self, timeout: float = config.THREAD_SHUTDOWN_TIMEOUT) -> bool:
        """
        Stop all workers gracefully.
        
        Returns:
            True if all stopped cleanly, False if any timed out
        """
        with self.lock:
            workers_list = list(self.workers.values())
        
        success = True
        for worker in workers_list:
            if not worker.stop(timeout=timeout):
                success = False
        
        logger.info(f"All workers stopped (success={success})")
        return success
    
    def get_status(self) -> Dict:
        """Get status of all workers."""
        with self.lock:
            return {
                name: worker.get_status()
                for name, worker in self.workers.items()
            }
    
    def worker_count(self) -> int:
        """Get number of registered workers."""
        with self.lock:
            return len(self.workers)


# Global thread manager instance
THREAD_MANAGER = ThreadManager()
