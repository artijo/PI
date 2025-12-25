import cv2
import time
import argparse
import sys
import numpy as np
import subprocess
import glob

from storage_module import get_usb_storage_path
from camera_module import LibCameraSubprocessReader, USBCameraReader, MockCameraReader, get_libcamera_list

def detect_cameras_smart(force_csi_count=0):
    """
    Returns a list of initialized CameraReader objects.
    force_csi_count: If > 0, blindly attempt to add this many CSI cameras even if detection fails.
    """
    readers = []
    
    # 1. Detect CSI Cameras (Libcamera)
    print("Scanning for CSI/Libcamera devices...")
    csi_indices = get_libcamera_list()
    if not csi_indices and force_csi_count > 0:
        print(f"Warning: Detection failed, but forcing {force_csi_count} CSI cameras as requested.")
        csi_indices = list(range(force_csi_count))
        
    print(f"Found/Forced CSI indices: {csi_indices}")
    
    for idx in csi_indices:
        name = f"CSI_Cam_{idx}"
        try:
            reader = LibCameraSubprocessReader(idx, name=name)
            readers.append(reader)
        except Exception as e:
            print(f"Failed to init CSI Camera {idx}: {e}")

    # 2. Detect USB Cameras
    # On Pi 5, USB cameras appear as /dev/video* but so do CSI media nodes (usually).
    # CSI media nodes usually don't support standard V4L2 capture in the same way or are busy.
    # We want to find the specific USB nodes.
    
    print("Scanning for USB V4L2 devices...")
    usb_candidates = []
    try:
        # Use v4l2-ctl to find devices that are definitely USB
        # Output of --list-devices groups by card name.
        result = subprocess.check_output(["v4l2-ctl", "--list-devices"], stderr=subprocess.STDOUT).decode("utf-8")
        
        current_card = ""
        lines = result.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line: 
                i+=1
                continue
            
            if not line.startswith("/"): # It's a card name
                current_card = line
                # Next lines are device paths
                i += 1
                while i < len(lines) and lines[i].strip().startswith("/"):
                    dev_path = lines[i].strip()
                    # Filter
                    # We accept it if the card name looks like a USB cam (not "bcm2835-isp", "unicam", "pi", etc)
                    # "unicam" is the CSI driver
                    # "bcm2835-isp" is ISP
                    is_internal = "unicam" in current_card.lower() or "bcm2835" in current_card.lower() or "raspberry" in current_card.lower() or "platform" in current_card.lower()
                    
                    if not is_internal:
                        # It's likely a USB camera
                        # We usually only want the first device node (video capture), not metadata
                        if dev_path not in usb_candidates:
                             # We can check specific caps later, but for now add it
                             usb_candidates.append(dev_path)
                             # Break inner loop to only take first node per device? 
                             # Often video0 is capture, video1 is metadata. Safe to take first.
                             break 
                    i += 1
                continue
            i+=1
    except Exception as e:
        print(f"v4l2-ctl scan failed: {e}. Falling back to glob.")
        usb_candidates = glob.glob("/dev/video*")

    print(f"Potential USB candidates: {usb_candidates}")
    
    for dev in usb_candidates:
        # Prevent double adding if user accidentally plugged something recognized as internal?
        # Just try to open.
        try:
             # Test open
             cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
             if cap.isOpened():
                 ret, _ = cap.read()
                 cap.release()
                 if ret:
                     # Add it
                     readers.append(USBCameraReader(dev, name=f"USB_{dev}"))
                     pass
                 else:
                     print(f"{dev} opened but yielded no frame.")
             else:
                 print(f"Could not open {dev}")
        except Exception as e:
             print(f"Error checking {dev}: {e}")

    return readers

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--force-csi", type=int, default=0, help="Force number of CSI cameras")
    parser.add_argument("--interval", type=int, default=300)
    args = parser.parse_args()

    storage = get_usb_storage_path()
    print(f"Storage: {storage}")
    
    readers = []
    
    if args.mock:
        readers = [
            MockCameraReader(0, "Mock_Cam_1"),
            MockCameraReader(1, "Mock_Cam_2"),
            MockCameraReader(2, "Mock_Cam_3")
        ]
    else:
        # If user knows they have CSI cameras but detection fails, they can use --force-csi
        # But we can also auto-infer from config if get_libcamera_list does it logic.
        readers = detect_cameras_smart(force_csi_count=args.force_csi)
        
    print(f"Detected {len(readers)} cameras.")
    
    if not readers:
        print("No cameras found.")
        # Don't exit immediately, maybe just running for debug?
        # But for recording we need cams.
        return

    # Start them
    for r in readers:
        r.start()

    # 3. Setup Recorders
    from recorder_module import VideoRecorder
    recorders = []
    for r in readers:
        rec = VideoRecorder(r, storage, split_interval=args.interval)
        rec.start()
        recorders.append(rec)

    # 4. Preview
    # Optimize preview to update less frequently (e.g. 15 FPS) to save CPU for recording
    last_preview_time = 0
    preview_interval = 1.0 / 15.0  # 15 FPS preview
    show_preview = True # Toggle flag
    
    print("\n[System] Running! Press 'q' to quit, 'p' to toggle preview.")
    
    try:
        while True:
            current_time = time.time()
            if current_time - last_preview_time > preview_interval:
                last_preview_time = current_time
                
                if show_preview:
                    # Combined preview (Heavy)
                    frames = []
                    for r in readers:
                            f = r.read()
                            if f is None:
                                f = np.zeros((480, 640, 3), dtype=np.uint8)
                                cv2.putText(f, "NO SIGNAL", (200, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,0,255), 2)
                            else:
                                f = cv2.resize(f, (640, 480), interpolation=cv2.INTER_NEAREST)
                            
                            cv2.putText(f, r.name, (10, 450), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)
                            frames.append(f)
                    
                    if frames:
                            vis = np.hstack(frames)
                            if vis.shape[1] > 1920:
                                scale = 1920 / vis.shape[1]
                                vis = cv2.resize(vis, (0,0), fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
                            
                            # Add overlay instructions
                            cv2.putText(vis, "Press 'p' for Dashboard Mode (Save CPU)", (10, 30), 
                                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                            cv2.imshow("Recorder", vis)
                else:
                    # Dashboard Mode (Lightweight)
                    # Just show a black screen with status text
                    dashboard = np.zeros((600, 800, 3), dtype=np.uint8)
                    cv2.putText(dashboard, "DASHBOARD MODE (Preview Off)", (50, 50), 
                                cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 255), 2)
                    
                    y_pos = 120
                    for i, rec in enumerate(recorders):
                        elapsed = time.time() - (rec.start_time if rec.start_time else time.time())
                        elapsed_str = time.strftime("%H:%M:%S", time.gmtime(elapsed))
                        status_color = (0, 255, 0) if rec.process else (0, 0, 255)
                        
                        text = f"Cam {i+1} ({rec.camera.name}): Recording... {elapsed_str}"
                        cv2.putText(dashboard, text, (50, y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2)
                        y_pos += 50
                        
                    cv2.putText(dashboard, "Press 'p' to Enable Preview", (50, y_pos + 50), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 1)
                    
                    cv2.imshow("Recorder", dashboard)
            
            key = cv2.waitKey(1)
            if key == ord('q'):
                 break
            elif key == ord('p'):
                show_preview = not show_preview
                # Clear window to avoid artifacts when switching sizes
                # cv2.destroyAllWindows() # Optional, might flicker
                pass
            
            # Small sleep to yield CPU
            time.sleep(0.01)
    except KeyboardInterrupt:
        print("\n[System] Interrupted by user.")
    finally:
        print("\n[System] Stopping all recordings. Please wait...")
        
        # Stop readers first (stop incoming data)
        for r in readers: 
            r.stop()
            
        # Stop recorders (flush and save files)
        for i, rec in enumerate(recorders):
            print(f"[System] Saving video {i+1}/{len(recorders)}...")
            rec.stop()
            
        cv2.destroyAllWindows()
        print("[System] All files saved successfully. Exiting.")

if __name__ == "__main__":
    main()
