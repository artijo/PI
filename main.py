import cv2
import time
import argparse
import sys
import numpy as np
import subprocess
import glob

from storage_module import get_usb_storage_path
from camera_module import LibCameraReader, USBCameraReader, MockCameraReader, get_libcamera_list

def detect_cameras_smart():
    """
    Returns a list of initialized CameraReader objects 
    based on what is actually connected.
    """
    readers = []
    
    # 1. Detect CSI Cameras (Libcamera)
    # This covers the 'Pi Camera' and the '3rd party Serial port (CSI)' camera
    print("Scanning for CSI/Libcamera devices...")
    csi_indices = get_libcamera_list()
    print(f"Found CSI indices: {csi_indices}")
    
    for idx in csi_indices:
        name = f"CSI_Cam_{idx}"
        try:
            reader = LibCameraReader(idx, name=name)
            # We can't easily verify if it works without starting, but let's assume valid
            readers.append(reader)
        except Exception as e:
            print(f"Failed to init CSI Camera {idx}: {e}")

    # 2. Detect USB Cameras
    # We look for /dev/video* but we must exclude what Libcamera might be using?
    # Usually libcamera doesn't claim /dev/video nodes in a blocking way unless legacy stack is on.
    # But usually USB cams are distinct.
    print("Scanning for USB V4L2 devices...")
    
    # On Pi, USB cams usually show up.
    # We'll try to identify them. 'v4l2-ctl --list-devices' is best.
    usb_candidates = []
    try:
        # Output looks like:
        # USB Camera Name (usb-....):
        #    /dev/video0
        #    /dev/video1
        result = subprocess.check_output(["v4l2-ctl", "--list-devices"], stderr=subprocess.STDOUT).decode("utf-8")
        
        current_device_name = ""
        for line in result.splitlines():
            if not line.startswith("\t"):
                current_device_name = line.strip()
            else:
                dev_path = line.strip()
                # Exclude internal/platform devices if they appear here (usually 'platform' or 'bcm2835')
                # If "usb" or "USB" in name, it's a good candidate.
                if "usb" in current_device_name.lower():
                    # Usually we only want the first node (video0), not video1 (metadata)
                    # We can pick the first one encountered for each block.
                    if dev_path not in usb_candidates:
                         # We only want to add ONE path per device group ideally
                         # But let's add all unique candidates and filter by opening
                         usb_candidates.append(dev_path)
    except:
        # Fallback to glob
        usb_candidates = glob.glob("/dev/video*")

    print(f"Potential USB candidates: {usb_candidates}")
    
    # We need to filter these. Open checks.
    # Also, we need to map them to 'Camera 3'.
    
    # Heuristic: Try to open. If success, keep.
    # Note: if CSI indices match the scan, we shouldn't double add.
    # But USB cams are usually /dev/videoN.
    
    verified_usb = []
    for dev in usb_candidates:
        # Only check a few, don't spam
        if len(verified_usb) >= 2: break 
        
        # Avoid duplication if we already have it?
        # Just try to open with USBCameraReader logic
        # Skip if index is high and busy?
        
        # We need to ensure we don't pick a busy device
        pass
        
    # Actually, simpler: just try to open candidates that are NOT used by CSI?
    # CSI uses internal ISP.
    
    # Let's just try to add the first working USB cam found
    for dev in usb_candidates:
        cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
        if cap.isOpened():
            # Check if we can read one frame
            ret, _ = cap.read()
            cap.release()
            if ret:
                readers.append(USBCameraReader(dev, name=f"USB_{dev}"))
                break # Only need 1 USB cam for now based on requirements? 
                      # Requirement: "Webcam connected to USB"
        else:
            print(f"Skipping {dev} (failed to open)")

    return readers

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true")
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
        readers = detect_cameras_smart()
        
    print(f"Detected {len(readers)} cameras.")
    
    if not readers:
        print("No cameras found. Exiting.")
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
    try:
        while True:
            # Combined preview
             frames = []
             for r in readers:
                 f = r.read()
                 if f is None:
                     f = np.zeros((480, 640, 3), dtype=np.uint8)
                 else:
                     f = cv2.resize(f, (640, 480))
                     # Label
                     cv2.putText(f, r.name, (10, 450), cv2.FONT_HERSHEY_SIMPLEX, 1, (255,255,255), 2)
                 frames.append(f)
             
             if frames:
                 # Stack depending on count
                 # Horizontal stack
                 vis = np.hstack(frames)
                 # Resize to fit screen if needed (max width 1920?)
                 if vis.shape[1] > 1920:
                     scale = 1920 / vis.shape[1]
                     vis = cv2.resize(vis, (0,0), fx=scale, fy=scale)
                     
                 cv2.imshow("Recorder", vis)
            
             if cv2.waitKey(10) == ord('q'):
                 break
    except KeyboardInterrupt:
        pass
    finally:
        for r in readers: r.stop()
        for rec in recorders: rec.stop()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
