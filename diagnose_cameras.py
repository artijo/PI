import cv2
import glob
import os

def check_cameras():
    print("Checking for /dev/video* entries...")
    video_devices = sorted(glob.glob("/dev/video*"))
    if not video_devices:
        print("No /dev/video* devices found.")
        return

    print(f"Found devices: {video_devices}")

    for dev in video_devices:
        idx = int(dev.replace("/dev/video", ""))
        print(f"\n--- Testing Camera Index {idx} ({dev}) ---")
        
        # 1. Try V4L2 backend
        print("  [Attempt 1] Standard V4L2...")
        cap = cv2.VideoCapture(idx, cv2.CAP_V4L2)
        if cap.isOpened():
            ret, frame = cap.read()
            if ret:
                h, w = frame.shape[:2]
                print(f"    SUCCESS: Resolution {w}x{h}")
            else:
                print("    OPENED but failed to read frame.")
            cap.release()
        else:
            print("    FAILED to open.")

        # 2. Try GStreamer (libcamerasrc) - Only likely to work for idx 0 usually, but let's try
        # This is for Pi Camera on Pi 5/Bookworm
        print("  [Attempt 2] GStreamer libcamerasrc...")
        # Note: libcamerasrc doesn't take an index directly in the same way, usually it's camera-name
        # But we can try a generic pipeline.
        gst_pipe = f"libcamerasrc camera-name='/base/soc/i2c0mux/i2c@1/imx219@10' ! video/x-raw, width=640, height=480 ! videoconvert ! appsink" 
        # The above logic is too specific. Let's try generic.
        gst_pipe_generic = "libcamerasrc ! video/x-raw, width=640, height=480, framerate=30/1 ! videoconvert ! appsink"
        
        # We only really need to test this once, not per index
        pass

    print("\n--- Testing GStreamer (libcamerasrc) ---")
    gst_pipe = "libcamerasrc ! video/x-raw, width=640, height=480, framerate=30/1 ! videoconvert ! appsink"
    cap = cv2.VideoCapture(gst_pipe, cv2.CAP_GSTREAMER)
    if cap.isOpened():
        ret, frame = cap.read()
        if ret:
             h, w = frame.shape[:2]
             print(f"  SUCCESS: Resolution {w}x{h}")
        else:
             print("  OPENED but failed to read frame.")
        cap.release()
    else:
        print("  FAILED to open or GStreamer not supported.")

if __name__ == "__main__":
    check_cameras()
