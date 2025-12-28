#!/usr/bin/env python3
"""
CAMAI - Jetson AI Camera System
Main entry point with FastAPI web dashboard.

Usage:
    python run.py              # Run with defaults
    python run.py --debug      # Debug mode
"""
import signal
import sys
import time
import logging
import threading
import asyncio
from pathlib import Path

# Add src to path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

from config import get_config
from capture import RTSPCapture
from detector import YOLODetector, Detection
from events import EventDetector
from notifications import NotificationManager
from stream import StreamServer, annotate_frame, extract_face_crop
from ptz import PTZController, PTZConfig
from pose import PoseEstimator
import api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("camai")


def run_fastapi(port: int):
    """Run FastAPI server in a thread."""
    import uvicorn
    uvicorn.run(api.app, host="0.0.0.0", port=port, log_level="warning")


def main():
    cfg = get_config()

    if cfg.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    log.info("=" * 50)
    log.info("CAMAI - Jetson AI Camera System")
    log.info("=" * 50)

    # Initialize components
    capture = RTSPCapture(cfg.rtsp_url, cfg.capture_width, cfg.capture_height)
    detector = YOLODetector(cfg.model_path, cfg.confidence, cfg.iou_threshold,
                            cfg.target_classes, cfg.class_names)
    events = EventDetector(cfg.person_dwell_time, cfg.person_cooldown, cfg.vehicle_stop_time)
    notifier = NotificationManager()

    # Setup notifications
    notifier.add_file_logger(cfg.log_dir, cfg.snapshot_dir)
    if cfg.enable_discord and cfg.discord_webhook:
        notifier.add_discord(cfg.discord_webhook)
    if cfg.enable_mqtt:
        notifier.add_mqtt(cfg.mqtt_broker, cfg.mqtt_port, cfg.mqtt_topic)

    # Event callback
    def on_event(event):
        frame = capture.read()
        notifier.notify(event, frame)
        # Add to API events
        api.add_event(event.to_dict())

    events.on_event(on_event)

    # Stream server (for frame storage, used by API)
    stream = StreamServer(cfg.stream_port)

    # PTZ tracking
    ptz = None
    if cfg.enable_ptz:
        ptz_config = PTZConfig(
            enabled=True,
            host=cfg.ptz_host,
            port=cfg.ptz_port,
            username=cfg.ptz_username,
            password=cfg.ptz_password,
            track_speed=cfg.ptz_track_speed,
            deadzone=cfg.ptz_deadzone,
            return_home=cfg.ptz_return_home,
            home_delay=cfg.ptz_home_delay,
        )
        ptz = PTZController(ptz_config)
        if ptz.connect():
            log.info("PTZ tracking enabled - will follow people")
        else:
            log.warning("PTZ connection failed - tracking disabled")
            ptz = None

    # Load model
    log.info(f"Loading model: {cfg.model_path}")
    detector.load()
    log.info(f"Model loaded. Inference: ~{detector.inference_ms:.1f}ms")

    # Pose estimation (optional)
    pose = None
    if cfg.enable_pose:
        pose = PoseEstimator(cfg.pose_model_path, cfg.confidence)
        if pose.load():
            log.info("Pose estimation enabled")
        else:
            log.warning("Pose estimation disabled - model failed to load")
            pose = None

    # Set API state
    api.set_state("config", cfg)
    api.set_state("detector", detector)
    api.set_state("capture", capture)
    api.set_state("events", events)
    api.set_state("ptz", ptz)
    api.set_state("pose", pose)
    api.set_state("stream_server", stream)

    # Start components
    capture.start()
    notifier.start()

    # Start FastAPI in a thread
    api_thread = threading.Thread(target=run_fastapi, args=(cfg.stream_port,), daemon=True)
    api_thread.start()

    log.info(f"Dashboard: http://0.0.0.0:{cfg.stream_port}")
    log.info(f"Stream: http://0.0.0.0:{cfg.stream_port}/stream")
    log.info(f"Face stream: http://0.0.0.0:{cfg.stream_port}/face")

    # Signal handling
    running = True
    def stop(sig, frame):
        nonlocal running
        log.info("Stopping...")
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    log.info("Running. Press Ctrl+C to stop.")

    # Main loop
    frame_count = 0
    start_time = time.time()
    last_log = time.time()
    last_stats_update = time.time()

    try:
        while running:
            frame = capture.read()
            if frame is None:
                time.sleep(0.01)
                continue

            frame_count += 1

            # Detect
            detections = detector.detect(frame)

            # Update events
            _ = events.update(detections, frame.shape[1], frame.shape[0])

            # PTZ tracking (follows people only)
            if ptz and cfg.enable_ptz:
                ptz.track_person(detections, frame.shape[1], frame.shape[0])

            # Pose estimation (optional)
            keypoints = None
            if pose and cfg.enable_pose:
                keypoints = pose.estimate(frame)

            # Update stream frames
            elapsed = time.time() - start_time
            fps = frame_count / elapsed if elapsed > 0 else 0
            total_inf = detector.inference_ms + (pose.inference_ms if pose else 0)

            # Face zoom uses RAW frame (no overlays)
            face_crop = extract_face_crop(frame, detections, keypoints)
            stream.update_face(face_crop)

            # Main stream gets annotations
            annotated = annotate_frame(frame, detections, fps, total_inf, keypoints)
            stream.update(annotated)

            # Update API stats periodically
            if time.time() - last_stats_update >= 0.5:
                api.update_stats(fps, total_inf, frame_count, events.tracked_count, elapsed)
                last_stats_update = time.time()

            # Log stats every 30s
            if time.time() - last_log >= 30:
                log.info(f"FPS: {fps:.1f} | Inference: {total_inf:.1f}ms | "
                        f"Frames: {frame_count} | Tracked: {events.tracked_count}")
                last_log = time.time()

    finally:
        capture.stop()
        notifier.stop()
        if ptz:
            ptz.disconnect()

        elapsed = time.time() - start_time
        log.info(f"Processed {frame_count} frames in {elapsed:.0f}s ({frame_count/elapsed:.1f} FPS)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="CAMAI - AI Camera System")
    parser.add_argument("--debug", action="store_true", help="Debug mode")
    args = parser.parse_args()

    if args.debug:
        get_config().debug = True

    main()
