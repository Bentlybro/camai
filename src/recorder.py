"""Video clip recorder for event captures."""
import threading
import time
import logging
from pathlib import Path
from collections import deque
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)


class ClipRecorder:
    """Records video clips when events occur.

    Maintains a rolling buffer of frames (pre-event) and captures
    post-event frames to create a complete clip.
    """

    def __init__(
        self,
        output_dir: str = "clips",
        pre_seconds: float = 3.0,
        post_seconds: float = 3.0,
        fps: int = 15,
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.pre_seconds = pre_seconds
        self.post_seconds = post_seconds
        self.fps = fps

        # Rolling buffer for pre-event frames
        buffer_size = int(pre_seconds * fps) + 10
        self._buffer = deque(maxlen=buffer_size)
        self._buffer_lock = threading.Lock()

        # Recording state
        self._recording = False
        self._record_frames = []
        self._record_until = 0
        self._record_event = None
        self._record_lock = threading.Lock()

        # Frame dimensions (set when first frame arrives)
        self._width = 0
        self._height = 0

    def add_frame(self, frame: np.ndarray):
        """Add frame to rolling buffer."""
        if frame is None:
            return

        # Store dimensions
        if self._height == 0:
            self._height, self._width = frame.shape[:2]

        with self._buffer_lock:
            # Store frame with timestamp
            self._buffer.append((time.time(), frame.copy()))

        # Check if we're recording post-event frames
        with self._record_lock:
            if self._recording:
                self._record_frames.append(frame.copy())

                # Check if recording is complete
                if time.time() >= self._record_until:
                    self._save_clip()

    def trigger(self, event_type: str, event_class: str) -> Optional[str]:
        """Trigger clip recording for an event.

        Returns the expected clip path (clip will be saved async).
        """
        with self._record_lock:
            if self._recording:
                # Already recording, skip
                return None

            # Start recording
            self._recording = True
            self._record_until = time.time() + self.post_seconds
            self._record_event = {
                "type": event_type,
                "class": event_class,
                "timestamp": time.time(),
            }

            # Copy pre-event buffer
            with self._buffer_lock:
                self._record_frames = [f[1].copy() for f in self._buffer]

            # Generate clip path
            ts = time.strftime("%Y%m%d_%H%M%S")
            safe_type = event_type.replace("_", "-")
            clip_name = f"{safe_type}_{ts}.mp4"
            clip_path = self.output_dir / clip_name

            logger.info(f"Recording clip: {clip_name}")
            return f"/api/clips/{clip_name}"

    def _save_clip(self):
        """Save recorded frames to video file."""
        try:
            import cv2

            if not self._record_frames:
                logger.warning("No frames to save")
                self._recording = False
                return

            ts = time.strftime("%Y%m%d_%H%M%S")
            event_type = self._record_event.get("type", "event").replace("_", "-")
            clip_name = f"{event_type}_{ts}.mp4"
            clip_path = self.output_dir / clip_name

            # Use mp4v codec
            fourcc = cv2.VideoWriter_fourcc(*'mp4v')
            writer = cv2.VideoWriter(
                str(clip_path),
                fourcc,
                self.fps,
                (self._width, self._height)
            )

            if not writer.isOpened():
                logger.error(f"Failed to open video writer for {clip_path}")
                self._recording = False
                self._record_frames = []
                return

            # Write all frames
            for frame in self._record_frames:
                if frame.shape[:2] == (self._height, self._width):
                    writer.write(frame)

            writer.release()
            logger.info(f"Saved clip: {clip_path} ({len(self._record_frames)} frames)")

        except Exception as e:
            logger.error(f"Failed to save clip: {e}")
        finally:
            self._recording = False
            self._record_frames = []
            self._record_event = None

    def get_clip_path(self, filename: str) -> Optional[Path]:
        """Get full path for a clip filename."""
        path = self.output_dir / filename
        if path.exists():
            return path
        return None
