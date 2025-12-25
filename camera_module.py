import cv2
import datetime
import time
import subprocess
import os
import numpy as np
from threading import Thread, Lock

def get_libcamera_list():
    """
    Parses 'libcamera-hello --list-cameras' (or rpicam-hello) to find available CSI cameras.
    Returns a list of camera IDs (index) or names.
    On Pi 5, these are managed by libcamera.
    """
    commands_to_try = [["libcamera-hello", "--list-cameras"], ["rpicam-hello", "--list-cameras"]]
    
    for cmd in commands_to_try:
        try:
            result = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
            output = result.decode("utf-8")
            # print(f"DEBUG: Output of {cmd}: \n{output}") # For debugging
            
            cameras = []
            for line in output.splitlines():
                # Line format example: 0 : imx708 [4608x2592] (/base/soc/i2c0mux/i2c@1/imx708@1a)
                if " : " in line and ("/base/" in line or "/platform/" in line):
                    parts = line.split(" : ")
                    if len(parts) > 0:
                        try:
                            idx = int(parts[0].strip())
                            cameras.append(idx)
                        except ValueError:
                            pass
            if cameras:
                return cameras
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
            
    # Fallback: Check /boot/firmware/config.txt for enabled cameras
    # This is a heuristic. If we see camera overlays, we assume they exist.
    print("Warning: Libcamera tools failed. Checking config.txt...")
    try:
        with open("/boot/firmware/config.txt", "r") as f:
            content = f.read()
            # Count how many camera overlays are uncommented
            # e.g., dtoverlay=imx708, dtoverlay=ov9281
            cam_count = 0
            if "dtoverlay=imx" in content and not "#dtoverlay=imx" in content: cam_count += 1 # Rough check
            if "dtoverlay=ov" in content and not "#dtoverlay=ov" in content: cam_count += 1
            
            # Better check: scan lines
            detected_cams = []
            for line in content.splitlines():
                line = line.strip()
                if line.startswith("dtoverlay=") and ("imx" in line or "ov" in line):
                     detected_cams.append(len(detected_cams)) # Assign hypothetical index 0, 1
            
            if detected_cams:
                print(f"Inferred {len(detected_cams)} cameras from config.txt")
                return detected_cams
    except Exception as e:
        print(f"Config check failed: {e}")

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
        self.fps = 25

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

class LibCameraSubprocessReader(BaseCameraReader):
    def __init__(self, camera_index, name="Pi-Camera"):
        super().__init__(name)
        self.camera_index = camera_index
        self.process = None
        self.width = 640
        self.height = 480
        # YUV420p size = w * h * 1.5
        self.frame_len = int(self.width * self.height * 1.5)
        
        # Detect executable
        self.cmd_tool = "libcamera-vid"
        try:
            subprocess.check_call(["rpicam-vid", "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.cmd_tool = "rpicam-vid"
        except:
            pass
            
        # Build command
        # --width 1920 --height 1080 (1080p) -> Reverted to 1280x720 safety
        # --codec yuv420 (raw yuv)
        # --annotate REMOVED (not supported on this version)
        cmd = [
            self.cmd_tool,
            "--camera", str(self.camera_index),
            "--width", "1280",
            "--height", "720",
            "--framerate", "15", # Reduced to 15 FPS for stability
            "--codec", "yuv420",
            "-t", "0",
            "--nopreview",
            "-o", "-"
        ]
        
        # Update internal dims for buffer calculation
        self.width = 1280
        self.height = 720
        
        # IMPORTANT: Update parent class attributes so Recorder sees correct size immediately
        self.frame_width = self.width
        self.frame_height = self.height
        
        self.frame_len = int(self.width * self.height * 1.5)
        
        print(f"[{self.name}] Starting process: {' '.join(cmd)}")
        # IMPORTANT: Capture stderr to debug why it fails
        self.process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=10**6)
        
        # Check immediately if it died
        time.sleep(0.5)
        if self.process.poll() is not None:
             err = self.process.stderr.read().decode('utf-8', errors='ignore')
             print(f"[{self.name}] FATAL: Process died immediately! Error:\n{err}")
        
        BaseCameraReader.start(self) # call parent start logic (thread)

    def update(self):
        while self.running and self.process.poll() is None:
            try:
                # Read exactly frame_len bytes
                # We need to handle blocking here carefully or checking poll()
                raw_data = self.process.stdout.read(self.frame_len)
                
                if not raw_data:
                    # Stream ended
                    if self.process.poll() is not None:
                         err = self.process.stderr.read().decode('utf-8', errors='ignore')
                         print(f"[{self.name}] Process exited. Error log:\n{err}")
                    break
                    
                if len(raw_data) != self.frame_len:
                    # Incomplete frame
                    continue
                
                # Convert YUV420 to BGR
                # buffer is Y (w*h) + U (w/2*h/2) + V (w/2*h/2)
                yuv = np.frombuffer(raw_data, dtype=np.uint8).reshape((int(self.height * 1.5), self.width))
                bgr = cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420)
                
                # Restore Python timestamp drawing since --annotate failed
                self._add_timestamp(bgr)
                
                with self.lock:
                    self.latest_frame = bgr
                    # already set in start(), but good to keep sync
                    self.frame_width = self.width
                    self.frame_height = self.height
            except Exception as e:
                print(f"[{self.name}] Error reading frame: {e}")
                time.sleep(0.1)

        if self.process:
            self.process.terminate()
            self.process.wait()

    def stop(self):
        super().stop()
        if self.process:
            self.process.terminate()
            self.process.wait()

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
        
        # PIPELINE:
        # We need a robust pipeline for OpenCV.
        # - videoconvert needed to handle YUV -> BGR for OpenCV
        # - appsink drop=true sync=false to prevent buffering lag and blocking
        cam_name = self._resolve_camera_name(camera_index)
        
        # PIPELINE:
        # We need a robust pipeline for OpenCV.
        # - videoconvert needed to handle YUV -> BGR for OpenCV
        # - appsink drop=true sync=false to prevent buffering lag and blocking
        cam_name = self._resolve_camera_name(camera_index)
        
        # IMPROVED PIPELINE:
        # 1. Source with camera-name
        # 2. explicit format filter after source (letting it pick default format but declaring video/x-raw)
        # 3. videoconvert
        # 4. explicit BGR format
        # 5. appsink
        
        # Note for OV9281: It's monochrome. libcamerasrc might output Y/GREY.
        
        self.pipeline = (
            f"libcamerasrc camera-name={cam_name} ! "
            "video/x-raw ! "  # Intermediate caps to ensure source negotiates properly
            "videoconvert ! "
            "video/x-raw, format=BGR ! "
            "appsink drop=true sync=false"
        )
        print(f"[{self.name}] Opening with pipeline: {self.pipeline}")
        self.cap = cv2.VideoCapture(self.pipeline, cv2.CAP_GSTREAMER)

        if not self.cap.isOpened():
             print(f"[{self.name}] Error: GStreamer pipeline failed to open.")

    def _resolve_camera_name(self, index):
        # rpicam-hello / libcamera-hello based name resolution
        commands = [["libcamera-hello", "--list-cameras"], ["rpicam-hello", "--list-cameras"]]
        for cmd in commands:
            try:
                result = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
                output = result.decode("utf-8")
                for line in output.splitlines():
                    if line.strip().startswith(str(index) + " :"):
                        import re
                        match = re.search(r'\((/base/.*?|/platform/.*?)\)', line)
                        if match:
                            return match.group(1)
            except:
                continue
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
        
        # IMPORTANT: Read back actual resolution!
        # If we requested 1280x720 but got 640x480, we must know.
        actual_w = self.cap.get(cv2.CAP_PROP_FRAME_WIDTH)
        actual_h = self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
        if actual_w > 0 and actual_h > 0:
            self.frame_width = int(actual_w)
            self.frame_height = int(actual_h)
            
        # Try to set anti-flicker to 50Hz (common cause of flickering)
        # usage: v4l2-ctl -d /dev/videoX -c power_line_frequency=1 (1=50Hz, 2=60Hz)
        try:
             subprocess.call(["v4l2-ctl", "-d", device_path, "-c", "power_line_frequency=1"], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except:
             pass

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

