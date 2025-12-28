#!/usr/bin/env python3
"""
CAMAI - Jetson AI Camera System
Main entry point.

Usage:
    python run.py              # Run with defaults
    python run.py --debug      # Debug mode
"""
import signal
import sys
import time
import logging
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("camai")


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

    events.on_event(on_event)

    # Stream server
    stream = None
    if cfg.enable_stream:
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

    # Start components
    capture.start()
    notifier.start()
    if stream:
        stream.start()

    # Signal handling
    running = True
    def stop(sig, frame):
        nonlocal running
        log.info("Stopping...")
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    log.info("Running. Press Ctrl+C to stop.")
    log.info(f"Stream: http://0.0.0.0:{cfg.stream_port}/stream")

    # Main loop
    frame_count = 0
    start_time = time.time()
    last_log = time.time()

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
            if ptz:
                ptz.track_person(detections, frame.shape[1], frame.shape[0])

            # Pose estimation (optional)
            keypoints = None
            if pose:
                keypoints = pose.estimate(frame)

            # Update stream
            if stream:
                elapsed = time.time() - start_time
                fps = frame_count / elapsed if elapsed > 0 else 0
                total_inf = detector.inference_ms + (pose.inference_ms if pose else 0)
                annotated = annotate_frame(frame, detections, fps, total_inf, keypoints)
                stream.update(annotated)

                # Update face zoom stream
                face_crop = extract_face_crop(frame, detections, keypoints)
                stream.update_face(face_crop)

            # Log stats every 30s
            if time.time() - last_log >= 30:
                elapsed = time.time() - start_time
                fps = frame_count / elapsed
                log.info(f"FPS: {fps:.1f} | Inference: {detector.inference_ms:.1f}ms | "
                        f"Frames: {frame_count} | Tracked: {events.tracked_count}")
                last_log = time.time()

    finally:
        capture.stop()
        notifier.stop()
        if stream:
            stream.stop()
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
