"""SQLite database for persistent event and stats storage."""
import sqlite3
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Default database path
DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "camai.db"


class Database:
    """SQLite database for CAMAI events and stats."""

    def __init__(self, db_path: Path = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database schema."""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            # Events table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    type TEXT NOT NULL,
                    class_name TEXT,
                    confidence REAL,
                    color TEXT,
                    description TEXT,
                    snapshot_path TEXT,
                    bbox TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Index for faster timestamp queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_timestamp
                ON events(timestamp DESC)
            """)

            # Index for type queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_type
                ON events(type)
            """)

            # Daily stats table (aggregated daily statistics)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT UNIQUE NOT NULL,
                    person_events INTEGER DEFAULT 0,
                    vehicle_events INTEGER DEFAULT 0,
                    package_events INTEGER DEFAULT 0,
                    total_events INTEGER DEFAULT 0,
                    total_frames INTEGER DEFAULT 0,
                    avg_fps REAL DEFAULT 0,
                    avg_inference_ms REAL DEFAULT 0,
                    uptime_seconds REAL DEFAULT 0
                )
            """)

            # Hourly stats table (for activity charts)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS hourly_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    date TEXT NOT NULL,
                    hour INTEGER NOT NULL,
                    event_count INTEGER DEFAULT 0,
                    person_count INTEGER DEFAULT 0,
                    vehicle_count INTEGER DEFAULT 0,
                    package_count INTEGER DEFAULT 0,
                    UNIQUE(date, hour)
                )
            """)

            # Recordings table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS recordings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    path TEXT NOT NULL,
                    start_time REAL NOT NULL,
                    end_time REAL,
                    duration REAL,
                    trigger_type TEXT DEFAULT 'person',
                    thumbnail_path TEXT,
                    file_size INTEGER DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Index for faster timestamp queries on recordings
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_recordings_start_time
                ON recordings(start_time DESC)
            """)

            # Users table for authentication
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    approved INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    last_login DATETIME
                )
            """)

            # Sessions table for token management
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    token TEXT UNIQUE NOT NULL,
                    stream_token TEXT UNIQUE,
                    stream_token_expires DATETIME,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    expires_at DATETIME NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
            """)

            # Index for faster token lookups
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_token
                ON sessions(token)
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_stream_token
                ON sessions(stream_token)
            """)

            conn.commit()
            logger.info(f"Database initialized: {self.db_path}")

    @contextmanager
    def _get_conn(self):
        """Get database connection with context manager."""
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def add_event(self, event: Dict) -> int:
        """Add an event to the database. Returns event ID."""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            # Extract event data
            timestamp = event.get("timestamp", time.time())
            event_type = event.get("type", "unknown")
            class_name = event.get("class_name", "")
            confidence = event.get("confidence", 0)
            color = event.get("color", "")
            description = event.get("description", "")
            snapshot_path = event.get("snapshot_path", "")
            bbox = json.dumps(event.get("bbox", [])) if event.get("bbox") else ""

            cursor.execute("""
                INSERT INTO events (timestamp, type, class_name, confidence,
                                   color, description, snapshot_path, bbox)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (timestamp, event_type, class_name, confidence,
                  color, description, snapshot_path, bbox))

            event_id = cursor.lastrowid

            # Update hourly stats
            dt = datetime.fromtimestamp(timestamp)
            date_str = dt.strftime("%Y-%m-%d")
            hour = dt.hour

            # Determine event category
            is_person = "person" in event_type
            is_vehicle = "vehicle" in event_type
            is_package = "package" in event_type

            cursor.execute("""
                INSERT INTO hourly_stats (date, hour, event_count, person_count,
                                         vehicle_count, package_count)
                VALUES (?, ?, 1, ?, ?, ?)
                ON CONFLICT(date, hour) DO UPDATE SET
                    event_count = event_count + 1,
                    person_count = person_count + ?,
                    vehicle_count = vehicle_count + ?,
                    package_count = package_count + ?
            """, (date_str, hour,
                  1 if is_person else 0,
                  1 if is_vehicle else 0,
                  1 if is_package else 0,
                  1 if is_person else 0,
                  1 if is_vehicle else 0,
                  1 if is_package else 0))

            # Update daily stats
            cursor.execute("""
                INSERT INTO daily_stats (date, person_events, vehicle_events,
                                        package_events, total_events)
                VALUES (?, ?, ?, ?, 1)
                ON CONFLICT(date) DO UPDATE SET
                    person_events = person_events + ?,
                    vehicle_events = vehicle_events + ?,
                    package_events = package_events + ?,
                    total_events = total_events + 1
            """, (date_str,
                  1 if is_person else 0,
                  1 if is_vehicle else 0,
                  1 if is_package else 0,
                  1 if is_person else 0,
                  1 if is_vehicle else 0,
                  1 if is_package else 0))

            conn.commit()
            return event_id

    def get_events(self, limit: int = 50, offset: int = 0,
                   event_type: str = None, since: float = None) -> List[Dict]:
        """Get events with optional filtering."""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM events WHERE 1=1"
            params = []

            if event_type:
                query += " AND type LIKE ?"
                params.append(f"%{event_type}%")

            if since:
                query += " AND timestamp >= ?"
                params.append(since)

            query += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor.execute(query, params)
            rows = cursor.fetchall()

            events = []
            for row in rows:
                event = dict(row)
                if event.get("bbox"):
                    try:
                        event["bbox"] = json.loads(event["bbox"])
                    except (json.JSONDecodeError, TypeError, ValueError):
                        event["bbox"] = []
                events.append(event)

            return events

    def get_event_count(self, since: float = None) -> int:
        """Get total event count."""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            if since:
                cursor.execute(
                    "SELECT COUNT(*) FROM events WHERE timestamp >= ?",
                    (since,)
                )
            else:
                cursor.execute("SELECT COUNT(*) FROM events")

            return cursor.fetchone()[0]

    def get_today_stats(self) -> Dict:
        """Get today's statistics."""
        today = datetime.now().strftime("%Y-%m-%d")

        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM daily_stats WHERE date = ?
            """, (today,))

            row = cursor.fetchone()
            if row:
                return dict(row)

            return {
                "date": today,
                "person_events": 0,
                "vehicle_events": 0,
                "package_events": 0,
                "total_events": 0,
            }

    def get_hourly_stats(self, date: str = None) -> List[Dict]:
        """Get hourly stats for a given date (defaults to today)."""
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM hourly_stats WHERE date = ? ORDER BY hour
            """, (date,))

            rows = cursor.fetchall()

            # Build full 24-hour data
            hourly_data = {h: {"hour": h, "count": 0, "person": 0,
                               "vehicle": 0, "package": 0} for h in range(24)}

            for row in rows:
                h = row["hour"]
                hourly_data[h] = {
                    "hour": h,
                    "count": row["event_count"],
                    "person": row["person_count"],
                    "vehicle": row["vehicle_count"],
                    "package": row["package_count"],
                }

            return [hourly_data[h] for h in range(24)]

    def get_daily_stats(self, days: int = 7) -> List[Dict]:
        """Get daily stats for the past N days."""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            start_date = (datetime.now() - timedelta(days=days-1)).strftime("%Y-%m-%d")

            cursor.execute("""
                SELECT * FROM daily_stats
                WHERE date >= ?
                ORDER BY date DESC
            """, (start_date,))

            rows = cursor.fetchall()
            stats_by_date = {row["date"]: dict(row) for row in rows}

            # Build full date range
            result = []
            for i in range(days - 1, -1, -1):
                date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
                if date in stats_by_date:
                    result.append(stats_by_date[date])
                else:
                    result.append({
                        "date": date,
                        "person_events": 0,
                        "vehicle_events": 0,
                        "package_events": 0,
                        "total_events": 0,
                    })

            return result

    def update_daily_runtime_stats(self, fps: float, inference_ms: float,
                                    frame_count: int, uptime: float):
        """Update today's runtime statistics."""
        today = datetime.now().strftime("%Y-%m-%d")

        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO daily_stats (date, total_frames, avg_fps,
                                        avg_inference_ms, uptime_seconds)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    total_frames = ?,
                    avg_fps = ?,
                    avg_inference_ms = ?,
                    uptime_seconds = uptime_seconds + ?
            """, (today, frame_count, fps, inference_ms, uptime,
                  frame_count, fps, inference_ms, uptime))

            conn.commit()

    def get_all_time_stats(self) -> Dict:
        """Get all-time statistics."""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    COUNT(*) as total_events,
                    SUM(CASE WHEN type LIKE '%person%' THEN 1 ELSE 0 END) as person_events,
                    SUM(CASE WHEN type LIKE '%vehicle%' THEN 1 ELSE 0 END) as vehicle_events,
                    SUM(CASE WHEN type LIKE '%package%' THEN 1 ELSE 0 END) as package_events,
                    MIN(timestamp) as first_event,
                    MAX(timestamp) as last_event
                FROM events
            """)

            row = cursor.fetchone()
            return dict(row) if row else {}

    def cleanup_old_events(self, days_to_keep: int = 7):
        """Delete events and stats older than specified days."""
        cutoff = time.time() - (days_to_keep * 86400)
        cutoff_date = (datetime.now() - timedelta(days=days_to_keep)).strftime("%Y-%m-%d")

        with self._get_conn() as conn:
            cursor = conn.cursor()

            # Delete old events
            cursor.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
            deleted_events = cursor.rowcount

            # Delete old hourly stats
            cursor.execute("DELETE FROM hourly_stats WHERE date < ?", (cutoff_date,))
            deleted_hourly = cursor.rowcount

            # Delete old daily stats
            cursor.execute("DELETE FROM daily_stats WHERE date < ?", (cutoff_date,))
            deleted_daily = cursor.rowcount

            conn.commit()

            if deleted_events > 0 or deleted_hourly > 0 or deleted_daily > 0:
                logger.info(f"Cleanup: deleted {deleted_events} events, {deleted_hourly} hourly stats, {deleted_daily} daily stats older than {days_to_keep} days")

            return deleted_events

    # === RECORDINGS METHODS ===

    def add_recording(self, recording: Dict) -> int:
        """Add a recording to the database. Returns recording ID."""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO recordings (filename, path, start_time, end_time,
                                       duration, trigger_type, thumbnail_path, file_size)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                recording.get("filename", ""),
                recording.get("path", ""),
                recording.get("start_time", time.time()),
                recording.get("end_time"),
                recording.get("duration"),
                recording.get("trigger_type", "person"),
                recording.get("thumbnail_path", ""),
                recording.get("file_size", 0),
            ))

            conn.commit()
            return cursor.lastrowid

    def get_recordings(self, limit: int = 50, offset: int = 0,
                       date: str = None, since: float = None) -> List[Dict]:
        """Get recordings with optional filtering."""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            query = "SELECT * FROM recordings WHERE 1=1"
            params = []

            if date:
                # Filter by date (recordings starting on that date)
                start_of_day = datetime.strptime(date, "%Y-%m-%d").timestamp()
                end_of_day = start_of_day + 86400
                query += " AND start_time >= ? AND start_time < ?"
                params.extend([start_of_day, end_of_day])

            if since:
                query += " AND start_time >= ?"
                params.append(since)

            query += " ORDER BY start_time DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor.execute(query, params)
            rows = cursor.fetchall()

            return [dict(row) for row in rows]

    def get_recording(self, recording_id: int) -> Optional[Dict]:
        """Get a single recording by ID."""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM recordings WHERE id = ?", (recording_id,))
            row = cursor.fetchone()

            return dict(row) if row else None

    def delete_recording(self, recording_id: int) -> bool:
        """Delete a recording from the database."""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("DELETE FROM recordings WHERE id = ?", (recording_id,))
            conn.commit()

            return cursor.rowcount > 0

    def get_recording_stats(self) -> Dict:
        """Get recording statistics."""
        with self._get_conn() as conn:
            cursor = conn.cursor()

            cursor.execute("""
                SELECT
                    COUNT(*) as total_recordings,
                    SUM(duration) as total_duration,
                    SUM(file_size) as total_size,
                    MIN(start_time) as oldest,
                    MAX(start_time) as newest
                FROM recordings
            """)

            row = cursor.fetchone()
            if row:
                return {
                    "total_recordings": row["total_recordings"] or 0,
                    "total_duration": row["total_duration"] or 0,
                    "total_size": row["total_size"] or 0,
                    "oldest": row["oldest"],
                    "newest": row["newest"],
                }

            return {"total_recordings": 0, "total_duration": 0, "total_size": 0}

    def cleanup_old_recordings(self, days_to_keep: int = 30) -> List[str]:
        """Delete recording records older than specified days. Returns list of paths to delete."""
        cutoff = time.time() - (days_to_keep * 86400)

        with self._get_conn() as conn:
            cursor = conn.cursor()

            # Get paths of recordings to delete
            cursor.execute(
                "SELECT path, thumbnail_path FROM recordings WHERE start_time < ?",
                (cutoff,)
            )
            rows = cursor.fetchall()

            paths_to_delete = []
            for row in rows:
                if row["path"]:
                    paths_to_delete.append(row["path"])
                if row["thumbnail_path"]:
                    paths_to_delete.append(row["thumbnail_path"])

            # Delete from database
            cursor.execute("DELETE FROM recordings WHERE start_time < ?", (cutoff,))
            deleted_count = cursor.rowcount

            conn.commit()

            if deleted_count > 0:
                logger.info(f"Cleanup: removed {deleted_count} recording records older than {days_to_keep} days")

            return paths_to_delete


    # === USER METHODS ===

    def get_user_count(self) -> int:
        """Get total number of users."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM users")
            return cursor.fetchone()[0]

    def create_user(self, username: str, password_hash: str,
                    role: str = "user", approved: int = 0) -> Optional[Dict]:
        """Create a new user. Returns user dict or None if username exists."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    INSERT INTO users (username, password_hash, role, approved)
                    VALUES (?, ?, ?, ?)
                """, (username, password_hash, role, approved))
                conn.commit()

                return self.get_user_by_id(cursor.lastrowid)
            except sqlite3.IntegrityError:
                return None

    def get_user_by_id(self, user_id: int) -> Optional[Dict]:
        """Get user by ID."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_user_by_username(self, username: str) -> Optional[Dict]:
        """Get user by username."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_all_users(self) -> List[Dict]:
        """Get all users."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, role, approved, created_at, last_login FROM users ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def get_pending_users(self) -> List[Dict]:
        """Get users pending approval."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, username, role, created_at FROM users WHERE approved = 0 ORDER BY created_at DESC")
            return [dict(row) for row in cursor.fetchall()]

    def approve_user(self, user_id: int) -> bool:
        """Approve a user."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET approved = 1 WHERE id = ?", (user_id,))
            conn.commit()
            return cursor.rowcount > 0

    def update_user_role(self, user_id: int, role: str) -> bool:
        """Update user role."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
            conn.commit()
            return cursor.rowcount > 0

    def update_user_last_login(self, user_id: int):
        """Update user's last login time."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET last_login = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
            conn.commit()

    def delete_user(self, user_id: int) -> bool:
        """Delete a user and their sessions."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            # Delete sessions first
            cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            # Delete user
            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            return cursor.rowcount > 0

    def update_user_password(self, user_id: int, password_hash: str) -> bool:
        """Update user password."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
            conn.commit()
            return cursor.rowcount > 0

    # === SESSION METHODS ===

    def create_session(self, user_id: int, token: str, expires_at: datetime) -> int:
        """Create a new session. Returns session ID."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sessions (user_id, token, expires_at)
                VALUES (?, ?, ?)
            """, (user_id, token, expires_at))
            conn.commit()
            return cursor.lastrowid

    def get_session_by_token(self, token: str) -> Optional[Dict]:
        """Get session by token (includes user info)."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.*, u.username, u.role, u.approved
                FROM sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.token = ? AND s.expires_at > CURRENT_TIMESTAMP
            """, (token,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_stream_token(self, session_id: int, stream_token: str, expires_at: datetime) -> bool:
        """Update stream token for a session."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE sessions
                SET stream_token = ?, stream_token_expires = ?
                WHERE id = ?
            """, (stream_token, expires_at, session_id))
            conn.commit()
            return cursor.rowcount > 0

    def get_session_by_stream_token(self, stream_token: str) -> Optional[Dict]:
        """Get session by stream token."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT s.*, u.username, u.role, u.approved
                FROM sessions s
                JOIN users u ON s.user_id = u.id
                WHERE s.stream_token = ? AND s.stream_token_expires > CURRENT_TIMESTAMP
            """, (stream_token,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def delete_session(self, token: str) -> bool:
        """Delete a session by token."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions WHERE token = ?", (token,))
            conn.commit()
            return cursor.rowcount > 0

    def delete_user_sessions(self, user_id: int) -> int:
        """Delete all sessions for a user. Returns count deleted."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            conn.commit()
            return cursor.rowcount

    def cleanup_expired_sessions(self) -> int:
        """Delete expired sessions. Returns count deleted."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sessions WHERE expires_at < CURRENT_TIMESTAMP")
            conn.commit()
            deleted = cursor.rowcount
            if deleted > 0:
                logger.info(f"Cleaned up {deleted} expired sessions")
            return deleted


# Singleton instance
_db: Optional[Database] = None


def get_database() -> Database:
    """Get the database singleton."""
    global _db
    if _db is None:
        _db = Database()
    return _db


def init_database(db_path: Path = None) -> Database:
    """Initialize the database with optional custom path."""
    global _db
    _db = Database(db_path)
    return _db
