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
from recording import RecordingManager
from database import init_database, get_database
from fcm import get_firebase_service, FIREBASE_AVAILABLE
import api

# Also import from new modular structure (api uses this internally)
from api import app, set_state, update_stats, add_event, broadcast_alert, broadcast_detections

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
    db.cleanup_old_recordings(days_to_keep=30)

    # Initialize Firebase for push notifications
    firebase = None
    if FIREBASE_AVAILABLE:
        firebase = get_firebase_service()
        if firebase.initialized:
            log.info(f"Firebase initialized - {len(firebase.get_registered_devices())} devices registered")
        else:
            log.warning("Firebase credentials not found - push notifications disabled")
    else:
        log.warning("Firebase SDK not installed - run: pip install firebase-admin")

    # Note: RecordingManager cleanup runs after it's initialized below

    if cfg.enable_discord and cfg.discord_webhook:
        notifier.add_discord(cfg.discord_webhook)
    if cfg.enable_mqtt:
        notifier.add_mqtt(cfg.mqtt_broker, cfg.mqtt_port, cfg.mqtt_topic)

    # Stream server (for frame storage, used by API)
    stream = StreamServer(cfg.stream_port)

    # Recording manager - records video when person detected
    def on_recording_complete(info):
        """Callback when a recording is saved."""
        try:
            db.add_recording(info)
            log.info(f"Recording saved: {info['filename']} ({info['duration']:.1f}s)")
        except Exception as e:
            log.warning(f"Failed to save recording to database: {e}")

    def on_person_alert(alert_data):
        """Callback when person is detected - sends WebSocket and Firebase notifications."""
        # Send via WebSocket for in-app updates
        broadcast_alert(alert_data)

        # Send via Firebase for push notifications
        if firebase and firebase.initialized:
            try:
                detections = alert_data.get("detections", [])
                person_count = len([d for d in detections if d.get("class") == "person"])
                confidence = detections[0].get("confidence", 0.9) if detections else 0.9

                firebase.send_person_alert(
                    person_count=max(1, person_count),
                    confidence=confidence,
                    timestamp=alert_data.get("timestamp"),
                )
            except Exception as e:
                log.warning(f"Failed to send Firebase notification: {e}")

    recorder = RecordingManager(
        output_dir="recordings",
        buffer_seconds=5.0,
        post_record_seconds=5.0,
        retention_days=30,
        fps=15,
        resolution=(cfg.capture_width, cfg.capture_height),
        on_recording_complete=on_recording_complete,
        on_person_alert=on_person_alert,
    )

    # Run recording file cleanup on startup
    recorder.cleanup_old_recordings()

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
    api.set_state("recorder", recorder)
    api.set_state("firebase", firebase)

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

    # Main loop with optimized pipeline
    frame_count = 0
    start_time = time.time()
    last_log = time.time()
    last_stats_update = time.time()

    # Thread pool for parallel processing
    from concurrent.futures import ThreadPoolExecutor
    process_pool = ThreadPoolExecutor(max_workers=3)

    # Cache for FPS calculation (avoid division every frame)
    cached_fps = 0
    cached_total_inf = 0
    fps_update_interval = 10  # Update FPS every N frames

    try:
        while running:
            frame = capture.read()
            if frame is None:
                continue

            frame_count += 1
            frame_h, frame_w = frame.shape[:2]

            # === DETECTION (GPU) ===
            all_detections = detector.detect(frame)

            # Filter detections (fast, in-place)
            detections = [d for d in all_detections if
                         (d.class_name == "person" and cfg.detect_person) or
                         (d.class_name in ("car", "truck") and cfg.detect_vehicle) or
                         (d.class_name == "package" and cfg.detect_package)]

            # === TRACKING FIRST (copies cached classifications to detections) ===
            events.update(detections, frame_w, frame_h)

            # === PARALLEL PROCESSING ===
            keypoints = None
            people_detected = any(d.class_name == "person" for d in detections)

            pose_future = None

            # Submit pose estimation (if needed)
            if pose and cfg.enable_pose and people_detected:
                pose_future = process_pool.submit(pose.estimate, frame)

            # Classify only NEW detections (those without cached signature)
            if classifier and cfg.enable_classifier:
                classified_any = False
                for d in detections:
                    # Skip if already has signature (from tracking cache)
                    if d.signature:
                        continue
                    # Classify this new detection
                    result = classifier.classify(frame, d.bbox, d.class_name)
                    if result:
                        d.color = result.color
                        d.description = result.description
                        d.signature = f"{result.color}_{d.class_name}" if result.color else d.class_name
                        classified_any = True
                # Update tracked objects with new classifications
                if classified_any:
                    events.update_classifications(detections)

            # Wait for pose results
            if pose_future:
                keypoints = pose_future.result()

            # Store keypoints for notifications
            api.set_state("latest_keypoints", keypoints)

            # PTZ tracking
            if ptz and cfg.enable_ptz:
                ptz.track_person(detections, frame_w, frame_h)

            # === RECORDING (checks for person and records with pre-roll buffer) ===
            recorder.add_frame(frame, people_detected, detections)

            # === STREAM UPDATE (async, non-blocking) ===
            # Update FPS periodically (not every frame)
            if frame_count % fps_update_interval == 0:
                elapsed = time.time() - start_time
                cached_fps = frame_count / elapsed if elapsed > 0 else 0
                cached_total_inf = detector.inference_ms
                if pose and cfg.enable_pose and people_detected:
                    cached_total_inf += pose.inference_ms
                if classifier and cfg.enable_classifier and detections:
                    cached_total_inf += classifier.inference_ms

            # Annotate and update stream (encoding is async now)
            if cfg.show_overlays:
                clean = frame.copy()  # Save clean copy before annotation
                annotate_frame(frame, detections, cached_fps, cached_total_inf, keypoints)
                stream.update(frame, clean_frame=clean)
            else:
                stream.update(frame, clean_frame=frame)

            # === STATS UPDATE (throttled) ===
            now = time.time()
            if now - last_stats_update >= 0.5:
                elapsed = now - start_time
                api.update_stats(cached_fps, cached_total_inf, frame_count, events.tracked_count, elapsed)
                # Also broadcast detections via WebSocket
                broadcast_detections(detections)
                last_stats_update = now

            # Log stats every 30s
            if now - last_log >= 30:
                log.info(f"FPS: {cached_fps:.1f} | Inference: {cached_total_inf:.1f}ms | "
                        f"Frames: {frame_count} | Tracked: {events.tracked_count}")
                last_log = now

    finally:
        process_pool.shutdown(wait=False)
        capture.stop()
        stream.stop()
        notifier.stop()
        recorder.stop()
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
