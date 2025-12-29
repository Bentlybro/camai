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
from stream import StreamServer, annotate_frame
from ptz import PTZController, PTZConfig
from pose import PoseEstimator
from classifier import ImageClassifier
from database import init_database, get_database
import api

# Also import from new modular structure (api uses this internally)
from api import app, set_state, update_stats, add_event

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

    # Initialize database
    db = init_database()
    log.info(f"Database initialized: {db.db_path}")

    # Initialize components
    capture = RTSPCapture(cfg.rtsp_url, cfg.capture_width, cfg.capture_height)
    detector = YOLODetector(cfg.model_path, cfg.confidence, cfg.iou_threshold,
                            cfg.target_classes, cfg.class_names)
    events = EventDetector(cfg.person_dwell_time, cfg.person_cooldown, cfg.vehicle_stop_time)
    notifier = NotificationManager()

    # Setup notifications (with 7-day retention)
    notifier.add_file_logger(cfg.log_dir, cfg.snapshot_dir)

    # Run cleanup on startup
    if notifier._file_logger:
        notifier._file_logger.cleanup_old_files()
    db.cleanup_old_events(days_to_keep=7)

    if cfg.enable_discord and cfg.discord_webhook:
        notifier.add_discord(cfg.discord_webhook)
    if cfg.enable_mqtt:
        notifier.add_mqtt(cfg.mqtt_broker, cfg.mqtt_port, cfg.mqtt_topic)

    # Stream server (for frame storage, used by API)
    stream = StreamServer(cfg.stream_port)

    # Event callback
    def on_event(event):
        frame = capture.read()

        # Get keypoints from API state (stored by main loop before events.update)
        keypoints = api.get_state("latest_keypoints")

        # Get snapshot path before notification
        snapshot_path = notifier.get_snapshot_path(event, frame)

        # Send notification with keypoints for head extraction
        notifier.notify(event, frame, keypoints=keypoints)

        # Build event dict with snapshot path
        event_dict = event.to_dict()
        if snapshot_path:
            event_dict["snapshot_path"] = snapshot_path

        # Add to API events
        api.add_event(event_dict)

    events.on_event(on_event)

    # Start capture early so RTSP connects while models load
    capture.start()
    notifier.start()

    # === PARALLEL INITIALIZATION ===
    # Load models and connect PTZ in parallel for faster startup
    from concurrent.futures import ThreadPoolExecutor, as_completed

    ptz = None
    pose = None
    classifier = None
    load_results = {}

    def load_detector():
        log.info(f"Loading model: {cfg.model_path}")
        detector.load()
        log.info(f"Model loaded. Inference: ~{detector.inference_ms:.1f}ms")
        return ("detector", True)

    def load_pose():
        if not cfg.enable_pose:
            return ("pose", None)
        p = PoseEstimator(cfg.pose_model_path, cfg.confidence)
        if p.load():
            log.info("Pose estimation enabled")
            return ("pose", p)
        else:
            log.warning("Pose estimation disabled - model failed to load")
            return ("pose", None)

    def load_classifier():
        if not cfg.enable_classifier:
            return ("classifier", None)
        c = ImageClassifier(cfg.classifier_model_path, cfg.confidence)
        if c.load():
            log.info("Image classifier enabled")
            return ("classifier", c)
        else:
            log.warning("Image classifier disabled - model failed to load")
            return ("classifier", None)

    def connect_ptz():
        if not cfg.ptz_host:
            return ("ptz", None)
        ptz_config = PTZConfig(
            enabled=cfg.enable_ptz,
            host=cfg.ptz_host,
            port=cfg.ptz_port,
            username=cfg.ptz_username,
            password=cfg.ptz_password,
            track_speed=cfg.ptz_track_speed,
            deadzone=cfg.ptz_deadzone,
            return_home=cfg.ptz_return_home,
            home_delay=cfg.ptz_home_delay,
        )
        p = PTZController(ptz_config)
        if p.connect():
            if cfg.enable_ptz:
                log.info("PTZ connected - auto-tracking enabled")
            else:
                log.info("PTZ connected - manual control only")
            return ("ptz", p)
        else:
            log.warning("PTZ connection failed")
            return ("ptz", None)

    # Run all loading tasks in parallel
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(load_detector),
            executor.submit(load_pose),
            executor.submit(load_classifier),
            executor.submit(connect_ptz),
        ]
        for future in as_completed(futures):
            name, result = future.result()
            load_results[name] = result

    # Get results
    pose = load_results.get("pose")
    classifier = load_results.get("classifier")
    ptz = load_results.get("ptz")

    # Connect PTZ to event detector
    if ptz:
        events.set_ptz(ptz)

    # Set API state
    api.set_state("config", cfg)
    api.set_state("detector", detector)
    api.set_state("capture", capture)
    api.set_state("events", events)
    api.set_state("ptz", ptz)
    api.set_state("pose", pose)
    api.set_state("classifier", classifier)
    api.set_state("stream_server", stream)
    api.set_state("notifier", notifier)

    # Start FastAPI in a thread
    api_thread = threading.Thread(target=run_fastapi, args=(cfg.stream_port,), daemon=True)
    api_thread.start()

    log.info(f"Dashboard: http://0.0.0.0:{cfg.stream_port}")
    log.info(f"Stream: http://0.0.0.0:{cfg.stream_port}/stream")

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
            all_detections = detector.detect(frame)

            # Filter detections based on toggles
            detections = []
            for d in all_detections:
                if d.class_name == "person" and cfg.detect_person:
                    detections.append(d)
                elif d.class_name in ("car", "truck") and cfg.detect_vehicle:
                    detections.append(d)
                elif d.class_name == "package" and cfg.detect_package:
                    detections.append(d)

            # Classify detections for better identification (if enabled)
            if classifier and cfg.enable_classifier and detections:
                for d in detections:
                    result = classifier.classify(frame, d.bbox, d.class_name)
                    if result:
                        d.color = result.color
                        d.description = result.description
                        d.signature = f"{result.color}_{d.class_name}" if result.color else d.class_name

            # Pose estimation BEFORE events (so keypoints available for notifications)
            keypoints = None
            people_detected = any(d.class_name == "person" for d in detections)
            if pose and cfg.enable_pose and people_detected:
                keypoints = pose.estimate(frame)
                # Store keypoints in API state for notification access
                api.set_state("latest_keypoints", keypoints)
            else:
                api.set_state("latest_keypoints", None)

            # Update events
            _ = events.update(detections, frame.shape[1], frame.shape[0])

            # PTZ tracking (follows people only)
            if ptz and cfg.enable_ptz:
                ptz.track_person(detections, frame.shape[1], frame.shape[0])

            # Update stream frames
            elapsed = time.time() - start_time
            fps = frame_count / elapsed if elapsed > 0 else 0
            # Only count inference time for models that actually ran
            total_inf = detector.inference_ms
            if pose and cfg.enable_pose and people_detected:
                total_inf += pose.inference_ms
            if classifier and cfg.enable_classifier and detections:
                total_inf += classifier.inference_ms

            # Main stream gets annotations (if enabled)
            if cfg.show_overlays:
                annotated = annotate_frame(frame, detections, fps, total_inf, keypoints)
                stream.update(annotated, clean_frame=frame)
            else:
                stream.update(frame, clean_frame=frame)

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
