import os
# Ensure Matplotlib cache dir is set and created before any third-party imports
MPL_CONFIG_DIR = "/storage/.cache/matplotlib"
os.environ["MPLCONFIGDIR"] = MPL_CONFIG_DIR
try:
    # Create the directory if it doesn't exist; ignore errors if concurrent
    from pathlib import Path as _Path
    _Path(MPL_CONFIG_DIR).mkdir(parents=True, exist_ok=True)
except Exception:
    # If creation fails, proceed; Matplotlib will still attempt to use the env var
    pass

import json
import logging
import math
import sys
import time  # new import for cycle delay
from collections import deque
from pathlib import Path
from typing import List, Optional
from datetime import datetime, UTC

import cv2
import mediapipe as mp


# Hardcoded config keeps edge deployment simple and self-contained.
BASE_DIR = Path(__file__).parent
TEST_DATA_DIR = BASE_DIR / "test-data"
# REPORT_DIR = BASE_DIR / "result-reports"
REPORT_DIR = Path("/storage/result-reports")
LOG_LEVEL = "DEBUG"
EAR_THRESHOLD = 0.23
CONSEC_FRAMES = 15
SMOOTHING_WINDOW = 5
LEFT_EYE_IDX = [33, 160, 158, 133, 153, 144]
RIGHT_EYE_IDX = [263, 387, 385, 362, 380, 373]
VIDEO_EXTENSIONS = {".mp4", ".webm", ".avi", ".mov", ".mkv"}


def _euclidean(p1, p2) -> float:
    return math.dist((p1.x, p1.y), (p2.x, p2.y))


def _compute_ear(landmarks, eye_idx: List[int]) -> float:
    p1, p2, p3, p4, p5, p6 = [landmarks[i] for i in eye_idx]
    vertical1 = _euclidean(p2, p6)
    vertical2 = _euclidean(p3, p5)
    horizontal = _euclidean(p1, p4)
    return (vertical1 + vertical2) / (2.0 * horizontal)


def _log_frame(logger: logging.Logger, frame_index: int, fps: float, smoothed_ear: Optional[float]):
    if fps <= 0:
        return
    if frame_index % max(int(fps // 2) or 1, 1) == 0:
        logger.debug(
            "Frame %d | timestamp: %.2fs | smoothed EAR: %s",
            frame_index,
            frame_index / fps,
            f"{smoothed_ear:.3f}" if smoothed_ear is not None else "None",
        )


def _build_report(video_path: Path, fps: float, events: List[dict]) -> dict:
    return {
        "video": str(video_path),
        "fps": fps,
        "events": events,
        "threshold": EAR_THRESHOLD,
        "consec_frames": CONSEC_FRAMES,
        "smoothing_window": SMOOTHING_WINDOW,
    }


def _write_report(report: dict, destination: Path, logger: logging.Logger) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    logger.info("Report written to %s", destination)


def _finalize_event(events: List[dict], start_ts: float, end_ts: float, logger: logging.Logger, note: str) -> None:
    events.append({"start": float(start_ts), "end": float(end_ts)})
    logger.info("Event recorded: start %.2fs end %.2fs (%s)", start_ts, end_ts, note)


def _analyze_video(video_path: Path, logger: logging.Logger) -> dict:
    logger.info("Starting analysis for video: %s", video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    mp_face_mesh = mp.solutions.face_mesh
    face_mesh = mp_face_mesh.FaceMesh(max_num_faces=1)
    cap = cv2.VideoCapture(str(video_path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if fps <= 0:
        logger.warning("FPS not reported by video; defaulting to 30.0")
        fps = 30.0

    frame_index = 0
    ear_history: deque[float] = deque(maxlen=SMOOTHING_WINDOW)
    frame_counter = 0
    events = []
    current_event_start = None

    logger.debug(
        "Config -> threshold: %.3f, consec_frames: %d, smoothing_window: %d",
        EAR_THRESHOLD,
        CONSEC_FRAMES,
        SMOOTHING_WINDOW,
    )

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_index += 1
            timestamp = frame_index / fps
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = face_mesh.process(rgb)

            if result.multi_face_landmarks:
                lm = result.multi_face_landmarks[0].landmark
                left_ear = _compute_ear(lm, LEFT_EYE_IDX)
                right_ear = _compute_ear(lm, RIGHT_EYE_IDX)
                ear = (left_ear + right_ear) / 2.0
                ear_history.append(ear)
                smoothed_ear = sum(ear_history) / len(ear_history)
            else:
                smoothed_ear = None

            _log_frame(logger, frame_index, fps, smoothed_ear)

            if smoothed_ear is None:
                if frame_counter >= CONSEC_FRAMES and current_event_start is not None:
                    _finalize_event(events, current_event_start, timestamp, logger, "tracking lost")
                frame_counter = 0
                current_event_start = None
                continue

            if smoothed_ear < EAR_THRESHOLD:
                frame_counter += 1
                if frame_counter == CONSEC_FRAMES:
                    current_event_start = timestamp - (CONSEC_FRAMES / fps)
                    logger.info("Potential drowsiness detected starting at %.2fs", current_event_start)
            else:
                if frame_counter >= CONSEC_FRAMES and current_event_start is not None:
                    _finalize_event(events, current_event_start, timestamp, logger, "eyes reopened")
                frame_counter = 0
                current_event_start = None

        if frame_counter >= CONSEC_FRAMES and current_event_start is not None:
            _finalize_event(events, current_event_start, frame_index / fps, logger, "video ended")
    finally:
        cap.release()

    report = _build_report(video_path, fps, events)
    logger.info("Analysis complete. %d drowsiness events detected.", len(events))
    logger.debug(json.dumps(report, indent=2))
    return report


def _collect_videos(directory: Path) -> List[Path]:
    if not directory.exists():
        raise FileNotFoundError(f"Test data directory not found: {directory}")
    videos = sorted(
        [p for p in directory.iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS and p.is_file()]
    )
    if not videos:
        raise FileNotFoundError(f"No video files found in: {directory}")
    return videos


def main() -> int:
    # Verbose logging helps trace processing on constrained devices.
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    logger = logging.getLogger("drowsiness-demo")

    overall_status = 0
    logger.info("Starting continuous monitoring loop. App version is 1.0.7")

    try:
        while True:
            try:
                videos = _collect_videos(TEST_DATA_DIR)
            except FileNotFoundError as exc:
                logger.error(str(exc))
                overall_status = 1
                break

            logger.info("Found %d video(s) in %s", len(videos), TEST_DATA_DIR)

            run_timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
            logger.info("Processing cycle timestamp: %s", run_timestamp)

            cycle_status = 0

            for video_path in videos:
                report_path = REPORT_DIR / f"{video_path.stem}_{run_timestamp}.json"
                logger.info("App version is 1.0.7. Processing video %s", video_path)
                try:
                    report = _analyze_video(video_path, logger)
                    _write_report(report, report_path, logger)
                    print(json.dumps(report, indent=2))
                except FileNotFoundError as exc:
                    logger.error(str(exc))
                    cycle_status = 1
                except Exception:
                    logger.exception("Unexpected error during analysis of %s", video_path)
                    cycle_status = 1

            if cycle_status == 0:
                logger.info("Cycle complete. All reports saved to %s", REPORT_DIR)
            else:
                logger.warning("Cycle %s completed with errors.", run_timestamp)

            overall_status = cycle_status
            logger.info("Sleeping 10 seconds before the next cycle.")
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info("Continuous monitoring interrupted by user.")

    return overall_status


if __name__ == "__main__":
    main()
