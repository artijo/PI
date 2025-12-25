import cv2
import datetime
import time
import subprocess
import os
import numpy as np
from threading import Thread, Lock

def get_libcamera_list():
    """
    Parses 'libcamera-hello --list-cameras' to find available CSI cameras.
    Returns a list of camera IDs (index) or names.
    On Pi 5, these are managed by libcamera.
    """
    try:
        # Run libcamera-hello --list-cameras
        # Output format is typically:
        # 0 : imx219 [3280x2464] (/base/soc/i2c0mux/i2c@1/imx219@10)
        # 1 : imx219 [3280x2464] (/base/soc/i2c0mux/i2c@1/imx219@0)
        result = subprocess.check_output(["libcamera-hello", "--list-cameras"], stderr=subprocess.STDOUT)
        output = result.decode("utf-8")
        
        cameras = []
        for line in output.splitlines():
            if " : " in line and "/base/" in line:
                parts = line.split(" : ")
                if len(parts) > 0:
                    try:
                        idx = int(parts[0].strip())
                        cameras.append(idx)
                    except ValueError:
                        pass
        return cameras
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("Warning: libcamera-hello not found or failed. Assuming no CSI cameras or legacy stack.")
        return []

def get_v4l2_devices():
    """
    Returns list of /dev/videoX that are actual USB cameras (not metadata nodes).
    Uses v4l2-ctl if available, or simple glob with heuristics.
    """
    devices = []
    # Simple glob finding
    import glob
    candidates = glob.glob("/dev/video*")
    
    # Filter out likely metadata/PiCam-managed nodes if possible without opening
    # For now, we'll return all and let the opener try.
    # But usually USB cams are /dev/video0, video2, etc. (even numbers)
    return candidates

class BaseCameraReader:
    def __init__(self, name="Camera"):
        self.name = name
        self.running = False
        self.lock = Lock()
        self.latest_frame = None
        self.thread = None
        self.frame_width = 640
        self.frame_height = 480
        self.fps = 30

    def start(self):
        self.running = True
        self.thread = Thread(target=self.update)
        self.thread.daemon = True
        self.thread.start()
    
    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()

    def read(self):
        with self.lock:
            if self.latest_frame is not None:
                return self.latest_frame.copy()
            return None

    def update(self):
        pass # Override

    def _add_timestamp(self, frame):
        if frame is None: return
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.putText(frame, timestamp, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                    1, (0, 255, 0), 2, cv2.LINE_AA)

class LibCameraReader(BaseCameraReader):
    def __init__(self, camera_index, name="Pi-Camera"):
        super().__init__(name)
        self.camera_index = camera_index
        # Use libcamerasrc with camera-name not supported easily by index, 
        # but newer GStreamer might support camera-index or we rely on default order.
        # Actually `libcamerasrc camera-name=...` is best. 
        # But simpler: `libcamerasrc camera-name=/base/...` if we parsed it.
        # If we just use `libcamerasrc` it picks the first.
        # For multiple cameras, we need to map index or name.
        # Let's try attempting to find the camera name via scanning logic or just 
        # rely on the user providing it? 
        # For now, let's try a workaround: separate processes or distinct pipeline configs.
        # If camera_index == 0: ...
        # If camera_index == 1: ...
        
        # A robust way is passing the camera index to libcamerasrc property if supported?
        # It's not. We need the unique ID.
        pass
        # I will assume for now we might fail to distinguish 2 CSI cameras easily without parsing.
        
        # PIPELINE:
        # We need a pipeline that selects the camera.
        # If we can't select easily, we might just get the default one.
        
        # Let's try to construct a pipeline.
        self.pipeline = f"libcamerasrc camera-name={self._resolve_camera_name(camera_index)} ! video/x-raw, width=1280, height=720, framerate=30/1 ! videoconvert ! appsink"
        print(f"[{self.name}] Opening with pipeline: {self.pipeline}")
        self.cap = cv2.VideoCapture(self.pipeline, cv2.CAP_GSTREAMER)

    def _resolve_camera_name(self, index):
        # Run list-cameras again to find the device path for 'index'
        try:
            result = subprocess.check_output(["libcamera-hello", "--list-cameras"], stderr=subprocess.STDOUT)
            output = result.decode("utf-8")
            for line in output.splitlines():
                if line.strip().startswith(str(index) + " :"):
                    # Extract content in parenthesis
                    # 0 : imx219 [...] (/base/...)
                    import re
                    match = re.search(r'\((/base/.*?)\)', line)
                    if match:
                        return match.group(1)
        except:
            pass
        return "" # Default to empty (auto)

    def update(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                self._add_timestamp(frame)
                with self.lock:
                    self.latest_frame = frame
                    self.frame_width = frame.shape[1]
                    self.frame_height = frame.shape[0]
            else:
                time.sleep(0.1)
        self.cap.release()

class USBCameraReader(BaseCameraReader):
    def __init__(self, device_path, name="USB-Camera"):
        super().__init__(name)
        # device_path e.g. /dev/video0
        # Check if it works
        self.cap = cv2.VideoCapture(device_path, cv2.CAP_V4L2)
        if not self.cap.isOpened():
             # Fallback
             try:
                 idx = int(device_path.replace("/dev/video", ""))
                 self.cap = cv2.VideoCapture(idx)
             except:
                 pass
        
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    def update(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                self._add_timestamp(frame)
                with self.lock:
                    self.latest_frame = frame
                    self.frame_width = frame.shape[1]
                    self.frame_height = frame.shape[0]
            else:
                time.sleep(0.1)
        self.cap.release()

class MockCameraReader(BaseCameraReader):
    def __init__(self, index, name="Mock"):
        super().__init__(name)
        self.index = index
        self.frame_width = 1280
        self.frame_height = 720

    def update(self):
        counter = 0
        while self.running:
            frame = np.zeros((self.frame_height, self.frame_width, 3), dtype=np.uint8)
            cv2.putText(frame, f"{self.name} {counter}", (50, 300), 
                        cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 4)
            self._add_timestamp(frame)
            with self.lock:
                self.latest_frame = frame
            counter += 1
            time.sleep(0.033)

