#!/usr/bin/env python3
"""
EchoDB - Leader Election Module

Implements Redis-based leader election for high availability
auto-mirror worker deployment.

Features:
    - Redis-based distributed locks
    - Automatic lease renewal via heartbeat
    - Graceful leadership transfer
    - Split-brain prevention via TTL

Requirements:
    pip install redis

Environment Variables:
    REDIS_HOST: Redis server host (default: localhost)
    REDIS_PORT: Redis server port (default: 6379)
    REDIS_PASSWORD: Redis password (default: None)
    LEADER_ELECTION_TTL: Leadership lease TTL in seconds (default: 30)
    WORKER_ID: Unique worker identifier (default: auto-generated UUID)
"""

import logging
import threading
import time
import uuid
from typing import Optional

try:
    import redis
except ImportError:
    redis = None
    raise ImportError(
        "Redis package is required for leader election. "
        "Install it with: pip install redis"
    )

logger = logging.getLogger("echodb.leader_election")


class LeaderElection:
    """
    Redis-based leader election with TTL-based lease renewal.

    This implementation uses Redis SET with NX (only if not exists)
    and EX (expiration) to implement distributed locks. The leader
    continuously renews its lease via a heartbeat thread.

    Example:
        election = LeaderElection(
            redis_host="localhost",
            redis_port=6379,
            worker_id="worker-1",
            ttl=30
        )

        if election.acquire_leadership():
            # This worker is the leader
            process_notifications()

        # Leadership is automatically relinquished on stop
        election.stop()
    """

    def __init__(
        self,
        redis_host: str,
        redis_port: int,
        worker_id: Optional[str] = None,
        ttl: int = 30,
        redis_password: Optional[str] = None,
        redis_db: int = 0,
    ):
        """
        Initialize leader election.

        Args:
            redis_host: Redis server hostname or IP
            redis_port: Redis server port
            worker_id: Unique identifier for this worker instance
            ttl: Leadership lease time-to-live in seconds
            redis_password: Optional Redis authentication password
            redis_db: Redis database number
        """
        if redis is None:
            raise ImportError("Redis package is required")

        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.ttl = ttl
        self.lock_key = "echodb:auto_mirror:leader_lock"

        # Initialize Redis client
        # Build Redis connection parameters
        redis_params = {
            "host": redis_host,
            "port": redis_port,
            "db": redis_db,
            "decode_responses": True,
            "socket_connect_timeout": 5,
            "socket_timeout": 5,
        }

        # Only add password if it's not empty
        if redis_password:
            redis_params["password"] = redis_password

        self.redis_client = redis.Redis(**redis_params)

        # Leader state
        self.is_leader = False
        self.last_heartbeat: Optional[float] = None

        # Heartbeat thread management
        self._stop_event = threading.Event()
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        logger.debug(
            f"LeaderElection initialized: worker_id={self.worker_id}, "
            f"redis={redis_host}:{redis_port}, ttl={ttl}"
        )

    def acquire_leadership(self) -> bool:
        """
        Attempt to acquire leadership.

        Uses Redis SET with NX (only set if key doesn't exist)
        and EX (set expiration) to implement distributed lock.

        Returns:
            True if leadership was acquired, False otherwise

        Raises:
            redis.RedisError: If Redis connection fails
        """
        try:
            # Try to acquire lock
            result = self.redis_client.set(
                self.lock_key,
                self.worker_id,
                nx=True,  # Only set if key doesn't exist
                ex=self.ttl,  # Set expiration
            )

            if result:
                with self._lock:
                    self.is_leader = True
                    self.last_heartbeat = time.time()
                self.start_heartbeat()
                logger.info(f"✅ Worker {self.worker_id} acquired leadership")
                return True
            else:
                # Check who is the current leader
                current_leader = self.redis_client.get(self.lock_key)
                logger.debug(
                    f"⏳ Worker {self.worker_id} could not acquire leadership. "
                    f"Current leader: {current_leader}"
                )
                with self._lock:
                    self.is_leader = False
                return False

        except redis.RedisError as e:
            logger.error(f"❌ Redis error acquiring leadership: {e}")
            with self._lock:
                self.is_leader = False
            raise

    def start_heartbeat(self):
        """
        Start background thread to renew leadership lease.

        The heartbeat thread runs in the background and continuously
        renews the leadership lease by extending the key's TTL.
        """
        if self._heartbeat_thread is None or not self._heartbeat_thread.is_alive():
            self._stop_event.clear()
            self._heartbeat_thread = threading.Thread(
                target=self._heartbeat_loop,
                daemon=True,
                name=f"heartbeat-{self.worker_id}",
            )
            self._heartbeat_thread.start()
            logger.debug(f"Heartbeat thread started for {self.worker_id}")

    def _heartbeat_loop(self):
        """
        Continuously renew leadership lease.

        This method runs in a background thread and:
        1. Checks if we're still the leader
        2. Renews the lease by extending TTL
        3. Handles Redis connection errors
        4. Stops when _stop_event is set
        """
        logger.debug(f"Heartbeat loop started for {self.worker_id}")

        while not self._stop_event.is_set():
            try:
                # Verify we're still the leader before renewing
                current_leader = self.redis_client.get(self.lock_key)

                if current_leader == self.worker_id:
                    # Renew lease by extending TTL
                    self.redis_client.expire(self.lock_key, self.ttl)
                    with self._lock:
                        self.last_heartbeat = time.time()
                    logger.debug(
                        f"Heartbeat: Leadership lease renewed for {self.worker_id}"
                    )
                else:
                    # Lost leadership (key was taken by another worker)
                    logger.warning(
                        f"⚠️  Worker {self.worker_id} lost leadership to {current_leader}"
                    )
                    with self._lock:
                        self.is_leader = False
                    break

            except redis.RedisError as e:
                logger.error(f"❌ Redis error in heartbeat: {e}")
                # Consider leadership lost on Redis error
                with self._lock:
                    self.is_leader = False
                break

            # Wait for half the TTL before next heartbeat
            # This ensures we renew well before expiration
            self._stop_event.wait(timeout=self.ttl / 2)

        logger.debug(f"Heartbeat loop stopped for {self.worker_id}")

    def relinquish_leadership(self):
        """
        Gracefully give up leadership.

        Removes the leadership lock from Redis, allowing other
        workers to acquire it.
        """
        self._stop_event.set()

        if self.is_leader:
            try:
                current_leader = self.redis_client.get(self.lock_key)
                # Only delete if we're still the leader
                if current_leader == self.worker_id:
                    self.redis_client.delete(self.lock_key)
                    logger.info(f"📤 Worker {self.worker_id} relinquished leadership")
            except redis.RedisError as e:
                logger.error(f"❌ Redis error relinquishing leadership: {e}")

        with self._lock:
            self.is_leader = False

    def stop(self):
        """
        Stop leader election and cleanup resources.

        This method:
        1. Stops the heartbeat thread
        2. Relinquishes leadership if held
        3. Closes Redis connection
        """
        logger.info(f"Stopping leader election for {self.worker_id}")

        # Stop heartbeat thread
        self._stop_event.set()

        # Wait for heartbeat thread to finish
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=5)

        # Relinquish leadership
        self.relinquish_leadership()

        # Close Redis connection
        try:
            self.redis_client.close()
        except redis.RedisError as e:
            logger.error(f"❌ Error closing Redis connection: {e}")

        logger.info(f"Leader election stopped for {self.worker_id}")

    def get_current_leader(self) -> Optional[str]:
        """
        Get the current leader's worker ID.

        Returns:
            Current leader's worker ID, or None if no leader
        """
        try:
            return self.redis_client.get(self.lock_key)
        except redis.RedisError as e:
            logger.error(f"❌ Redis error getting current leader: {e}")
            return None

    @property
    def leadership_status(self) -> dict:
        """
        Get leadership status information.

        Returns:
            Dictionary with leadership status details
        """
        with self._lock:
            return {
                "worker_id": self.worker_id,
                "is_leader": self.is_leader,
                "last_heartbeat": self.last_heartbeat,
                "ttl": self.ttl,
                "lock_key": self.lock_key,
            }


# Example usage and testing
if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <redis_host> <redis_port>")
        sys.exit(1)

    redis_host = sys.argv[1]
    redis_port = int(sys.argv[2])

    election = LeaderElection(
        redis_host=redis_host,
        redis_port=redis_port,
        worker_id=f"test-worker-{uuid.uuid4().hex[:4]}",
        ttl=10,
    )

    try:
        if election.acquire_leadership():
            print("✅ Acquired leadership! Holding for 20 seconds...")
            time.sleep(20)
        else:
            print("⏳ Could not acquire leadership")
            current = election.get_current_leader()
            print(f"Current leader: {current}")

    finally:
        election.stop()
