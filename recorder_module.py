import cv2
import os
import datetime
import time
from threading import Thread
import platform

class VideoRecorder:
    def __init__(self, camera_reader, storage_path, split_interval=300):
        """
        split_interval: Time in seconds to split files (default 300s = 5 min)
        """
        self.camera = camera_reader
        self.base_storage_path = storage_path
        self.split_interval = split_interval
        
        self.running = False
        self.out = None
        self.current_filename = None
        self.start_time = None
        self.thread = None
        
        # Ensure base path exists
        if not os.path.exists(self.base_storage_path):
            try:
                os.makedirs(self.base_storage_path)
            except OSError as e:
                print(f"Error creating storage path: {e}")

    def _get_output_filepath(self):
        # Format: Base/CameraName/YYYY-MM-DD/HH-MM-SS.avi
        # Example: X:/Recordings/Camera0/2023-10-27/14-30-00.avi
        now = datetime.datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H-%M-%S")
        
        cam_dir = os.path.join(self.base_storage_path, self.camera.name, date_str)
        if not os.path.exists(cam_dir):
            os.makedirs(cam_dir)
            
        return os.path.join(cam_dir, f"{time_str}.avi")

    def _start_recording(self):
        filename = self._get_output_filepath()
        fourcc = cv2.VideoWriter_fourcc(*'XVID') # XVID is widely supported. MJPG is another option.
        
        # Use camera's actual resolution
        width = self.camera.frame_width
        height = self.camera.frame_height
        fps = self.camera.fps
        
        print(f"[{self.camera.name}] Starting recording to {filename} ({width}x{height} @ {fps}fps)")
        self.out = cv2.VideoWriter(filename, fourcc, fps, (width, height))
        self.start_time = time.time()
        self.current_filename = filename

    def _stop_recording(self):
        if self.out:
            self.out.release()
            print(f"[{self.camera.name}] Saved {self.current_filename}")
            self.out = None

    def start(self):
        self.running = True
        self.thread = Thread(target=self.record_loop)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
        self._stop_recording()

    def record_loop(self):
        self._start_recording()
        
        while self.running:
            frame = self.camera.read()
            if frame is not None:
                if self.out:
                     self.out.write(frame)
                
                # Check for split
                if time.time() - self.start_time > self.split_interval:
                    self._stop_recording()
                    self._start_recording()
            else:
                # No frame available, small sleep to prevent CPU spin
                time.sleep(0.01)
        
        self._stop_recording()
