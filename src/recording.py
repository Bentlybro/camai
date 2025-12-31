"""Recording manager - records video when person detected with pre-roll buffer."""
import os
import time
import threading
import logging
from collections import deque
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Callable
from dataclasses import dataclass
import cv2
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class RecordingInfo:
    """Information about a recording."""
    id: int
    filename: str
    path: str
    start_time: float
    end_time: float
    duration: float
    trigger_type: str
    thumbnail_path: str
    file_size: int
    created_at: str


class RecordingManager:
    """
    Manages video recording with pre-roll buffer.

    - Keeps a rolling buffer of frames (configurable, default 5 seconds)
    - When person detected, starts recording including buffered frames
    - Continues recording while person visible
    - Stops after cooldown period when no person detected
    - Saves to MP4 files with 30-day retention
    """

    def __init__(
        self,
        output_dir: str = "recordings",
        buffer_seconds: float = 5.0,
        post_record_seconds: float = 5.0,
        retention_days: int = 30,
        fps: int = 15,
        resolution: tuple = (1280, 720),
        on_recording_complete: Optional[Callable] = None,
        on_person_alert: Optional[Callable] = None,
    ):
        self.output_dir = Path(output_dir)
        self.buffer_seconds = buffer_seconds
        self.post_record_seconds = post_record_seconds
        self.retention_days = retention_days
        self.fps = fps
        self.resolution = resolution
        self.on_recording_complete = on_recording_complete
        self.on_person_alert = on_person_alert

        # Calculate buffer size based on FPS
        self._buffer_size = int(buffer_seconds * fps)
        self._frame_buffer = deque(maxlen=self._buffer_size)

        # Recording state
        self._recording = False
        self._writer: Optional[cv2.VideoWriter] = None
        self._current_file: Optional[Path] = None
        self._record_start: float = 0
        self._last_person_seen: float = 0
        self._person_visible = False
        self._trigger_frame: Optional[np.ndarray] = None

        # Thread safety
        self._lock = threading.Lock()

        # Detection threshold - require consecutive frames before triggering
        self._consecutive_detections = 0
        self._detection_threshold = 3  # Need 3+ consecutive frames (~0.2s at 15fps)

        # Alert cooldown (don't spam alerts)
        self._last_alert_time: float = 0
        self._alert_cooldown: float = 30.0  # 30 seconds between alerts

        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"RecordingManager initialized: buffer={buffer_seconds}s, retention={retention_days} days")

    def add_frame(self, frame: np.ndarray, person_detected: bool, detections: list = None):
        """
        Add a frame to the buffer and handle recording logic.

        Args:
            frame: The video frame
            person_detected: Whether a person is currently detected
            detections: List of detection objects (for alert info)
        """
        now = time.time()

        # Resize frame if needed
        if frame.shape[1] != self.resolution[0] or frame.shape[0] != self.resolution[1]:
            frame = cv2.resize(frame, self.resolution)

        with self._lock:
            # Always add to buffer (for pre-roll)
            self._frame_buffer.append((now, frame.copy()))

            if person_detected:
                self._consecutive_detections += 1
                self._last_person_seen = now

                # Only trigger after seeing person for threshold frames
                # This prevents false positives from single-frame detections
                if self._consecutive_detections >= self._detection_threshold:
                    if not self._person_visible:
                        # Person confirmed - start tracking
                        self._person_visible = True
                        self._trigger_frame = frame.copy()

                        # Send alert (with cooldown)
                        if now - self._last_alert_time >= self._alert_cooldown:
                            self._last_alert_time = now
                            self._send_alert(frame, detections)

                    if not self._recording:
                        # Start recording
                        self._start_recording(now)

            else:
                # Reset consecutive counter when no person detected
                self._consecutive_detections = 0

                if self._person_visible:
                    # Person just left view
                    self._person_visible = False

                if self._recording:
                    # Check if cooldown expired
                    if now - self._last_person_seen >= self.post_record_seconds:
                        self._stop_recording()

            # Write frame if recording
            if self._recording and self._writer:
                self._writer.write(frame)

    def _send_alert(self, frame: np.ndarray, detections: list = None):
        """Send person detection alert with screenshot."""
        if self.on_person_alert:
            try:
                # Encode frame as JPEG for efficient transfer
                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                jpeg_bytes = buffer.tobytes()

                alert_data = {
                    "type": "person_alert",
                    "timestamp": time.time(),
                    "screenshot": jpeg_bytes,
                    "detections": [
                        {
                            "class": d.class_name if hasattr(d, 'class_name') else d.get('class', 'person'),
                            "confidence": float(d.confidence) if hasattr(d, 'confidence') else d.get('confidence', 0.9),
                        }
                        for d in (detections or [])
                        if (hasattr(d, 'class_name') and d.class_name == 'person') or
                           (isinstance(d, dict) and d.get('class') == 'person')
                    ]
                }

                self.on_person_alert(alert_data)
            except Exception as e:
                logger.error(f"Failed to send alert: {e}")

    def _start_recording(self, start_time: float):
        """Start a new recording, including buffered frames."""
        # Create date-based subdirectory
        date_str = datetime.now().strftime("%Y-%m-%d")
        date_dir = self.output_dir / date_str
        date_dir.mkdir(parents=True, exist_ok=True)

        # Create filename
        timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"person_{timestamp_str}.mp4"
        self._current_file = date_dir / filename

        # Initialize video writer
        # On Jetson, H.264 hardware encoding may not be available via OpenCV
        # Use mp4v which works universally, videos can be re-encoded later if needed
        # For browser playback, the API will serve these files and most browsers
        # can play mp4v, though H.264 is preferred
        codecs_to_try = [
            'mp4v',  # MPEG-4 Part 2 - works on Jetson
            'XVID',  # Xvid - good fallback
            'MJPG',  # Motion JPEG - large files but very compatible
        ]

        self._writer = None
        for codec in codecs_to_try:
            try:
                fourcc = cv2.VideoWriter_fourcc(*codec)
                test_writer = cv2.VideoWriter(
                    str(self._current_file),
                    fourcc,
                    self.fps,
                    self.resolution
                )
                if test_writer.isOpened():
                    self._writer = test_writer
                    logger.info(f"Recording using codec: {codec}")
                    break
                else:
                    test_writer.release()
            except Exception as e:
                logger.debug(f"Codec {codec} not available: {e}")
                continue

        if not self._writer or not self._writer.isOpened():
            logger.error(f"Failed to open video writer: {self._current_file} (no compatible codec found)")
            self._writer = None
            return

        # Write buffered frames (pre-roll)
        buffer_frames_written = 0
        for ts, frame in self._frame_buffer:
            if frame.shape[1] != self.resolution[0] or frame.shape[0] != self.resolution[1]:
                frame = cv2.resize(frame, self.resolution)
            self._writer.write(frame)
            buffer_frames_written += 1

        self._recording = True
        self._record_start = start_time - (buffer_frames_written / self.fps)

        logger.info(f"Recording started: {self._current_file} (pre-roll: {buffer_frames_written} frames)")

    def _stop_recording(self):
        """Stop current recording and save."""
        if not self._recording or not self._writer:
            return

        self._writer.release()
        self._writer = None
        self._recording = False

        end_time = time.time()
        duration = end_time - self._record_start

        # Get file size
        file_size = self._current_file.stat().st_size if self._current_file.exists() else 0

        # Generate thumbnail
        thumbnail_path = self._generate_thumbnail()

        logger.info(f"Recording stopped: {self._current_file} ({duration:.1f}s, {file_size / 1024 / 1024:.1f}MB)")

        # Callback with recording info
        if self.on_recording_complete and self._current_file:
            info = {
                "filename": self._current_file.name,
                "path": str(self._current_file),
                "start_time": self._record_start,
                "end_time": end_time,
                "duration": duration,
                "trigger_type": "person",
                "thumbnail_path": thumbnail_path,
                "file_size": file_size,
            }
            try:
                self.on_recording_complete(info)
            except Exception as e:
                logger.error(f"Recording complete callback error: {e}")

        self._current_file = None
        self._trigger_frame = None

    def _generate_thumbnail(self) -> str:
        """Generate thumbnail from trigger frame."""
        if self._trigger_frame is None or self._current_file is None:
            return ""

        try:
            thumbnail_path = self._current_file.with_suffix('.jpg')

            # Resize to thumbnail size
            thumb = cv2.resize(self._trigger_frame, (320, 180))
            cv2.imwrite(str(thumbnail_path), thumb, [cv2.IMWRITE_JPEG_QUALITY, 85])

            return str(thumbnail_path)
        except Exception as e:
            logger.error(f"Failed to generate thumbnail: {e}")
            return ""

    def cleanup_old_recordings(self):
        """Delete recordings older than retention period."""
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        deleted_count = 0
        deleted_size = 0

        for date_dir in self.output_dir.iterdir():
            if not date_dir.is_dir():
                continue

            try:
                # Parse date from directory name
                dir_date = datetime.strptime(date_dir.name, "%Y-%m-%d")

                if dir_date < cutoff:
                    # Delete all files in directory
                    for f in date_dir.iterdir():
                        try:
                            size = f.stat().st_size
                            f.unlink()
                            deleted_count += 1
                            deleted_size += size
                        except Exception as e:
                            logger.warning(f"Failed to delete {f}: {e}")

                    # Try to remove empty directory
                    try:
                        date_dir.rmdir()
                    except:
                        pass
            except ValueError:
                # Not a date directory, skip
                continue

        if deleted_count > 0:
            logger.info(f"Cleanup: deleted {deleted_count} recordings ({deleted_size / 1024 / 1024:.1f}MB)")

        return deleted_count, deleted_size

    def get_recordings(self, date: str = None, limit: int = 50, offset: int = 0) -> list:
        """
        Get list of recordings, optionally filtered by date.

        Args:
            date: Filter by date (YYYY-MM-DD format)
            limit: Maximum number of recordings to return
            offset: Offset for pagination

        Returns:
            List of recording info dictionaries
        """
        recordings = []

        # Get directories to search
        if date:
            date_dirs = [self.output_dir / date]
        else:
            date_dirs = sorted(self.output_dir.iterdir(), reverse=True)

        for date_dir in date_dirs:
            if not date_dir.is_dir():
                continue

            for video_file in sorted(date_dir.glob("*.mp4"), reverse=True):
                if len(recordings) >= offset + limit:
                    break

                if len(recordings) < offset:
                    recordings.append(None)  # Placeholder for offset
                    continue

                try:
                    stat = video_file.stat()
                    thumbnail = video_file.with_suffix('.jpg')

                    # Parse timestamp from filename
                    parts = video_file.stem.split('_')
                    if len(parts) >= 3:
                        date_part = parts[1]
                        time_part = parts[2]
                        try:
                            dt = datetime.strptime(f"{date_part}_{time_part}", "%Y%m%d_%H%M%S")
                            start_time = dt.timestamp()
                        except:
                            start_time = stat.st_mtime
                    else:
                        start_time = stat.st_mtime

                    recordings.append({
                        "filename": video_file.name,
                        "path": str(video_file.relative_to(self.output_dir)),
                        "date": date_dir.name,
                        "start_time": start_time,
                        "file_size": stat.st_size,
                        "thumbnail": str(thumbnail.relative_to(self.output_dir)) if thumbnail.exists() else None,
                    })
                except Exception as e:
                    logger.warning(f"Error reading recording {video_file}: {e}")

        # Remove placeholders and return actual recordings
        return [r for r in recordings if r is not None][:limit]

    def get_recording_path(self, relative_path: str) -> Optional[Path]:
        """Get full path to a recording file."""
        full_path = self.output_dir / relative_path
        if full_path.exists() and full_path.is_file():
            return full_path
        return None

    def delete_recording(self, relative_path: str) -> bool:
        """Delete a recording and its thumbnail."""
        full_path = self.output_dir / relative_path

        if not full_path.exists():
            return False

        try:
            full_path.unlink()

            # Also delete thumbnail
            thumbnail = full_path.with_suffix('.jpg')
            if thumbnail.exists():
                thumbnail.unlink()

            # Try to remove empty parent directory
            try:
                full_path.parent.rmdir()
            except:
                pass

            return True
        except Exception as e:
            logger.error(f"Failed to delete recording {relative_path}: {e}")
            return False

    def get_storage_stats(self) -> dict:
        """Get storage statistics."""
        total_size = 0
        total_count = 0
        by_date = {}

        for date_dir in self.output_dir.iterdir():
            if not date_dir.is_dir():
                continue

            date_size = 0
            date_count = 0

            for f in date_dir.glob("*.mp4"):
                try:
                    size = f.stat().st_size
                    total_size += size
                    date_size += size
                    total_count += 1
                    date_count += 1
                except:
                    pass

            if date_count > 0:
                by_date[date_dir.name] = {
                    "count": date_count,
                    "size": date_size,
                }

        return {
            "total_recordings": total_count,
            "total_size": total_size,
            "total_size_gb": round(total_size / 1024 / 1024 / 1024, 2),
            "retention_days": self.retention_days,
            "by_date": by_date,
        }

    @property
    def is_recording(self) -> bool:
        """Check if currently recording."""
        return self._recording

    def stop(self):
        """Stop recording if active."""
        with self._lock:
            if self._recording:
                self._stop_recording()
