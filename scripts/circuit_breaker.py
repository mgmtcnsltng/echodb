#!/usr/bin/env python3
"""
EchoDB - Circuit Breaker Pattern Implementation

Implements the circuit breaker pattern to prevent cascading failures
and provide fast-fail when external services are unavailable.

Benefits:
    - Prevents Cascading Failures: Stops error propagation to dependent systems
    - Resource Conservation: No wasted retries on dead services
    - Automatic Recovery: HALF_OPEN state tests service health before full recovery
    - Observable: Clear metrics on circuit state for monitoring

States:
    - CLOSED: Normal operation, requests pass through
    - OPEN: Failing, requests are rejected immediately
    - HALF_OPEN: Testing if service has recovered

Example:
    breaker = CircuitBreaker(CircuitBreakerConfig(
        failure_threshold=5,
        success_threshold=2,
        timeout=60
    ))

    try:
        result = breaker.call(risky_function, arg1, arg2)
    except CircuitBreakerOpenError:
        # Circuit is open, handle failure gracefully
        logger.error("Service unavailable, circuit open")
"""

import time
import threading
from enum import Enum
from typing import Callable, Any, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger("echodb.circuit_breaker")


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation
    OPEN = "open"          # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing if service recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5      # Failures before opening
    success_threshold: int = 2      # Successes to close circuit
    timeout: int = 60               # Seconds before HALF_OPEN
    expected_exception: Exception = Exception
    name: str = "circuit_breaker"


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


class CircuitBreaker:
    """
    Circuit breaker pattern implementation.

    The circuit breaker monitors failures and opens the circuit
    when the failure threshold is reached. Once open, requests
    fail immediately (fast-fail) until the timeout expires,
    at which point it enters HALF_OPEN state to test if the
    service has recovered.

    Thread-safe implementation suitable for concurrent use.
    """

    def __init__(self, config: CircuitBreakerConfig):
        """
        Initialize circuit breaker.

        Args:
            config: Circuit breaker configuration
        """
        self.config = config
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = threading.Lock()

        logger.debug(
            f"CircuitBreaker '{config.name}' initialized: "
            f"failure_threshold={config.failure_threshold}, "
            f"timeout={config.timeout}s"
        )

    def call(self, func: Callable[..., Any], *args, **kwargs) -> Any:
        """
        Execute function through circuit breaker.

        Args:
            func: Function to execute
            *args: Positional arguments for function
            **kwargs: Keyword arguments for function

        Returns:
            Result of function execution

        Raises:
            CircuitBreakerOpenError: If circuit is open
            Exception: Any exception from the called function
        """
        if not self._allow_request():
            logger.warning(
                f"Circuit breaker '{self.config.name}' is {self._state.value}, "
                f"rejecting request to {func.__name__}"
            )
            raise CircuitBreakerOpenError(
                f"Circuit breaker '{self.config.name}' is {self._state.value}"
            )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _allow_request(self) -> bool:
        """
        Check if request should be allowed.

        Returns:
            True if request should proceed, False if it should be rejected
        """
        with self._lock:
            if self._state == CircuitState.CLOSED:
                # Normal operation, allow all requests
                return True

            if self._state == CircuitState.OPEN:
                # Check if timeout has expired
                if time.time() - self._last_failure_time >= self.config.timeout:
                    # Transition to HALF_OPEN to test recovery
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    logger.info(
                        f"Circuit breaker '{self.config.name}' timeout expired, "
                        f"transitioning to HALF_OPEN"
                    )
                    return True
                # Still in timeout period, reject request
                return False

            if self._state == CircuitState.HALF_OPEN:
                # Testing if service has recovered, allow request
                return True

        return False

    def _on_success(self):
        """Handle successful operation."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                # In HALF_OPEN, count successes toward closing circuit
                self._success_count += 1
                logger.debug(
                    f"Circuit breaker '{self.config.name}' success "
                    f"({self._success_count}/{self.config.success_threshold})"
                )

                if self._success_count >= self.config.success_threshold:
                    # Service has recovered, close circuit
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    logger.info(
                        f"Circuit breaker '{self.config.name}' recovered, "
                        f"transitioning to CLOSED"
                    )
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                self._failure_count = 0

    def _on_failure(self):
        """Handle failed operation."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            logger.warning(
                f"Circuit breaker '{self.config.name}' failure "
                f"({self._failure_count}/{self.config.failure_threshold})"
            )

            # Check if threshold reached
            if self._failure_count >= self.config.failure_threshold:
                if self._state != CircuitState.OPEN:
                    # Transition to OPEN
                    old_state = self._state
                    self._state = CircuitState.OPEN
                    logger.error(
                        f"Circuit breaker '{self.config.name}' threshold reached, "
                        f"transitioning from {old_state.value} to OPEN"
                    )

            if self._state == CircuitState.HALF_OPEN:
                # Failed during recovery test, go back to OPEN
                self._state = CircuitState.OPEN
                logger.error(
                    f"Circuit breaker '{self.config.name}' failed during "
                    f"HALF_OPEN test, returning to OPEN"
                )

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            return self._state

    @property
    def failure_count(self) -> int:
        """Get current failure count."""
        with self._lock:
            return self._failure_count

    @property
    def success_count(self) -> int:
        """Get current success count in HALF_OPEN."""
        with self._lock:
            return self._success_count

    def reset(self):
        """Reset circuit breaker to CLOSED state."""
        with self._lock:
            old_state = self._state
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None
            logger.info(
                f"Circuit breaker '{self.config.name}' reset "
                f"from {old_state.value} to CLOSED"
            )

    def get_status(self) -> dict:
        """
        Get circuit breaker status.

        Returns:
            Dictionary with status information
        """
        with self._lock:
            return {
                "name": self.config.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "failure_threshold": self.config.failure_threshold,
                "success_threshold": self.config.success_threshold,
                "timeout": self.config.timeout,
                "last_failure_time": self._last_failure_time,
                "time_until_half_open": (
                    max(0, self.config.timeout - (time.time() - self._last_failure_time))
                    if self._state == CircuitState.OPEN and self._last_failure_time
                    else None
                )
            }


# Example usage and testing
if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Create circuit breaker
    config = CircuitBreakerConfig(
        name="test_breaker",
        failure_threshold=3,
        success_threshold=2,
        timeout=5
    )
    breaker = CircuitBreaker(config)

    def failing_function():
        """Function that always fails."""
        raise Exception("Test failure")

    def working_function():
        """Function that succeeds."""
        return "Success!"

    print("Testing circuit breaker...")
    print("=" * 60)

    # Test threshold
    print("\n1. Testing failure threshold (3 failures):")
    for i in range(4):
        try:
            breaker.call(failing_function)
        except (CircuitBreakerOpenError, Exception) as e:
            print(f"   Attempt {i+1}: {type(e).__name__}: {e}")
        print(f"   State: {breaker.state.value}")

    print("\n2. Waiting for timeout (5s)...")
    time.sleep(5)
    print(f"   State: {breaker.state.value}")

    print("\n3. Testing recovery in HALF_OPEN:")
    for i in range(3):
        try:
            result = breaker.call(working_function)
            print(f"   Attempt {i+1}: {result}")
        except (CircuitBreakerOpenError, Exception) as e:
            print(f"   Attempt {i+1}: {type(e).__name__}: {e}")
        print(f"   State: {breaker.state.value}")

    print("\n" + "=" * 60)
    print("Final status:")
    status = breaker.get_status()
    for key, value in status.items():
        print(f"  {key}: {value}")
