# Drowsiness Detection using Eye Aspect Ratio (EAR)

This service detects potential driver drowsiness by monitoring eye closure over time using the Eye Aspect Ratio (EAR).

## How EAR Works
- EAR quantifies how “open” an eye is from facial landmarks (e.g., MediaPipe Face Mesh).
- For each eye, two vertical distances (between eyelids) and one horizontal distance (across the eye) are measured.
- Formula: EAR = (vertical1 + vertical2) / (2 × horizontal).
  - When the eye closes, vertical distances shrink → EAR drops.
  - When the eye is open, EAR stays higher and more stable.

## Algorithm Steps (as implemented in `src/my-service/main.py`)
1. Detect face and eye landmarks per frame (e.g., via MediaPipe Face Mesh).
2. Compute EAR for left and right eyes; average them for robustness.
3. Smooth the EAR with a short moving window to reduce noise.
4. Compare the smoothed EAR to a threshold (e.g., 0.23).
5. If EAR stays below the threshold for a minimum number of consecutive frames (e.g., 15), flag a drowsiness event.
6. Start and end timestamps are recorded for each event. If tracking is lost or the stream ends during an event, it is finalized accordingly.

## Outputs
- Events are saved with start/end timestamps and summary metrics under `src/my-service/result-reports/`.
