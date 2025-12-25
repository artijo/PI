import cv2
import time
import argparse
import sys
import numpy as np

from storage_module import get_usb_storage_path
from camera_module import CameraReader, scan_cameras
from recorder_module import VideoRecorder

def main():
    parser = argparse.ArgumentParser(description="Raspberry Pi Multi-Camera Recorder")
    parser.add_argument("--mock", action="store_true", help="Use mock cameras for testing")
    parser.add_argument("--interval", type=int, default=300, help="File split interval in seconds (default 300)")
    args = parser.parse_args()

    # 1. Setup Storage
    storage_path = get_usb_storage_path()
    print(f"Storage Path: {storage_path}")

    # 2. Setup Cameras
    cameras = []
    
    # We want exactly 3 cameras as per requirements
    # If not enough real cameras are found, we can warn user
    
    found_indices = scan_cameras()
    print(f"Found camera indices: {found_indices}")

    # Determine which indices to use
    # Requirement: 3 cameras (Pi native + Serial + USB)
    # The system sees them as /dev/video0, /dev/video1, etc.
    # We will try to map the first 3 available.
    
    target_indices = []
    if len(found_indices) >= 3:
        target_indices = found_indices[:3]
    elif len(found_indices) > 0:
        print(f"Warning: Only found {len(found_indices)} cameras. Using those.")
        target_indices = found_indices
    else:
        print("Error: No cameras found!")
        if not args.mock:
             # In production we might want to keep retrying, but for now exit or mock
             pass

    if args.mock:
        print("Running in MOCK mode. Creating 3 mock cameras.")
        # Create 3 mock cameras
        target_indices = [0, 1, 2]
    
    # Initialize Camera Readers
    cam_readers = []
    for i, idx in enumerate(target_indices):
        name = f"Camera_{idx}"
        if args.mock:
            from camera_module import MockCameraReader
            reader = MockCameraReader(idx, name=name)
        else:
            reader = CameraReader(idx, name=name)
        
        reader.start()
        cam_readers.append(reader)

    if not cam_readers:
        print("No cameras to record. Exiting.")
        return

    # 3. Setup Recorders
    recorders = []
    for reader in cam_readers:
        rec = VideoRecorder(reader, storage_path, split_interval=args.interval)
        rec.start()
        recorders.append(rec)

    print("Recording started. Press 'q' to quit.")

    # 4. Main Preview Loop
    try:
        while True:
            # Collect frames for preview
            preview_frames = []
            
            for reader in cam_readers:
                frame = reader.read()
                if frame is not None:
                    # Resize to something small for preview (e.g., height 240 to stack or grid)
                    # Requirement says "480p preview", maybe total window is 480p or each?
                    # "Preview will show only 480p ... and show all three cameras"
                    # Let's ensure the *combined* view is manageable or each is small.
                    # Let's resize each to width 320 (approx 240p) to fit 3 side-by-side or similar.
                    # Or keep them 480p height.
                    
                    # Let's target a manageable grid. 
                    # If we have 3 cameras, maybe 2x2 grid (one empty) or 3x1 row.
                    # Let's do a resize to fixed width of 400px.
                    
                    h, w = frame.shape[:2]
                    scale = 480 / h # Resize to 480p height
                    new_w = int(w * scale)
                    new_h = 480
                    
                    # Actually, user said preview resolution 480p to avoid load.
                    # Let's resize each frame to roughly 640x480.
                    resized = cv2.resize(frame, (640, 480))
                    
                    # Put label on preview
                    cv2.putText(resized, reader.name, (10, 450), cv2.FONT_HERSHEY_SIMPLEX, 
                                1, (255, 255, 255), 2)
                    
                    preview_frames.append(resized)
                else:
                    # Black placeholder
                    preview_frames.append(np.zeros((480, 640, 3), dtype=np.uint8))

            if preview_frames:
                # Concatenate horizontally
                combined = np.hstack(preview_frames)
                
                # If too wide, maybe resize the *combined* image to fit screen?
                # For now, just show it.
                # If 3 cameras: 640*3 = 1920 width. Fits most monitors.
                
                # Resize combined for preview window to be lighter?
                # User said "Preview ... 480p". 
                # If "Preview" means the window, then maybe the window is 480p *high*. 
                # This matches our construction.
                
                cv2.imshow('Multi-Camera Recorder', combined)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
                
            time.sleep(0.01)

    except KeyboardInterrupt:
        print("Stopping...")
    finally:
        # Cleanup
        for rec in recorders:
            rec.stop()
        for reader in cam_readers:
            reader.stop()
        cv2.destroyAllWindows()
        print("System exited cleanly.")

if __name__ == "__main__":
    main()
