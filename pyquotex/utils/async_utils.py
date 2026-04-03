"""Async utilities for improved performance with event-driven architecture."""
import asyncio
import orjson
from typing import Any, Dict, Optional, Callable


class AsyncEvent:
    """Enhanced asyncio.Event with timeout support and automatic reset."""
    
    def __init__(self, auto_reset: bool = False):
        self.event = asyncio.Event()
        self.auto_reset = auto_reset
        self.data: Optional[Any] = None
    
    async def wait(self, timeout: Optional[float] = None):
        """Wait for event with optional timeout."""
        try:
            await asyncio.wait_for(self.event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Event wait timeout after {timeout}s")
        
        data = self.data
        if self.auto_reset:
            self.reset()
        return data
    
    def set(self, data: Optional[Any] = None):
        """Set event and store data."""
        self.data = data
        self.event.set()
    
    def reset(self):
        """Reset event and clear data."""
        self.event.clear()
        self.data = None
    
    def is_set(self) -> bool:
        """Check if event is set."""
        return self.event.is_set()


class EventRegistry:
    """Registry for managing multiple events by key."""
    
    def __init__(self):
        self._events: Dict[str, AsyncEvent] = {}
        self._lock = asyncio.Lock()
    
    async def get_event(self, key: str, auto_reset: bool = False) -> AsyncEvent:
        """Get or create an event by key."""
        async with self._lock:
            if key not in self._events:
                self._events[key] = AsyncEvent(auto_reset=auto_reset)
            return self._events[key]
    
    async def set_event(self, key: str, data: Optional[Any] = None):
        """Set event data by key."""
        event = await self.get_event(key)
        event.set(data)
    
    async def wait_event(self, key: str, timeout: Optional[float] = None):
        """Wait for event by key."""
        event = await self.get_event(key)
        return await event.wait(timeout=timeout)
    
    async def clear_event(self, key: str):
        """Clear event by key."""
        async with self._lock:
            if key in self._events:
                self._events[key].reset()


class FastJSONParser:
    """Fast JSON parsing using orjson."""
    
    @staticmethod
    async def parse_async(data: bytes, skip_header: int = 0) -> Any:
        """Parse JSON data asynchronously, optionally skipping header bytes."""
        if skip_header > 0:
            data = data[skip_header:]

        # Offload to thread pool to avoid blocking
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, orjson.loads, data)

    @staticmethod
    def parse_sync(data: bytes, skip_header: int = 0) -> Any:
        """Parse JSON data synchronously."""
        if skip_header > 0:
            data = data[skip_header:]
        return orjson.loads(data)

    @staticmethod
    async def dumps_async(obj: Any) -> bytes:
        """Serialize object to JSON bytes asynchronously."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, orjson.dumps, obj)
    
    @staticmethod
    def dumps_sync(obj: Any) -> bytes:
        """Serialize object to JSON bytes synchronously."""
        return orjson.dumps(obj)


class AsyncTimeout:
    """Context manager for async operations with timeout."""

    def __init__(self, seconds: float, message: str = "Operation timeout"):
        self.seconds = seconds
        self.message = message
        self.task = None
        self._cancel_handle = None
        self._timed_out = False

    def _cancel_task(self):
        """Cancel the tracked task when the timeout expires."""
        self._timed_out = True
        if self.task is not None and not self.task.done():
            self.task.cancel()

    async def __aenter__(self):
        self.task = asyncio.current_task()
        loop = asyncio.get_running_loop()
        self._timed_out = False
        self._cancel_handle = loop.call_later(self.seconds, self._cancel_task)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._cancel_handle is not None:
            self._cancel_handle.cancel()
            self._cancel_handle = None

        if self._timed_out and exc_type is asyncio.CancelledError:
            raise TimeoutError(self.message)


async def wait_for_condition(
    condition_func: Callable[[], bool],
    timeout: float = 30.0,
    check_interval: float = 0.1,
    error_message: str = "Condition wait timeout"
) -> bool:
    """Wait for a condition to become true with timeout.
    
    Args:
        condition_func: Callable that returns bool
        timeout: Maximum wait time in seconds
        check_interval: Time between checks in seconds
        error_message: Error message if timeout occurs
    
    Returns:
        True if condition met
    
    Raises:
        TimeoutError: If condition not met within timeout
    """
    start_time = asyncio.get_event_loop().time()
    
    while True:
        if condition_func():
            return True
        
        elapsed = asyncio.get_event_loop().time() - start_time
        if elapsed > timeout:
            raise TimeoutError(error_message)
        
        await asyncio.sleep(min(check_interval, timeout - elapsed))


async def gather_with_limit(
    coros: list,
    limit: int = 10,
    return_exceptions: bool = False
) -> list:
    """Run coroutines with concurrency limit.
    
    Args:
        coros: List of coroutines
        limit: Maximum concurrent tasks
        return_exceptions: If True, return exceptions instead of raising
    
    Returns:
        List of results
    """
    semaphore = asyncio.Semaphore(limit)
    
    async def sem_coro(coro):
        async with semaphore:
            return await coro
    
    return await asyncio.gather(
        *[sem_coro(coro) for coro in coros],
        return_exceptions=return_exceptions
    )
