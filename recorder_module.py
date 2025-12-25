import cv2
import os
import datetime
import time
from threading import Thread
import queue
import subprocess
import shutil

class VideoRecorder:
    def __init__(self, camera_reader, storage_path, split_interval=300):
        self.camera = camera_reader
        self.base_storage_path = storage_path
        self.split_interval = split_interval
        
        self.running = False
        self.process = None
        self.current_filename = None
        self.start_time = None
        
        self.frame_queue = queue.Queue(maxsize=150) 
        
        # Check if ffmpeg is installed
        if not shutil.which("ffmpeg"):
            print("ERROR: ffmpeg is not installed! Please run 'sudo apt install ffmpeg'")

        if not os.path.exists(self.base_storage_path):
            try:
                os.makedirs(self.base_storage_path)
            except OSError as e:
                print(f"Error creating storage path: {e}")

    def _get_output_filepath(self):
        now = datetime.datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        time_str = now.strftime("%H-%M-%S")
        
        cam_dir = os.path.join(self.base_storage_path, self.camera.name, date_str)
        if not os.path.exists(cam_dir):
            os.makedirs(cam_dir)
            
        return os.path.join(cam_dir, f"{time_str}.mp4")

    def _start_recording(self):
        filename = self._get_output_filepath()
        width = self.camera.frame_width
        height = self.camera.frame_height
        fps = int(self.camera.fps)
        if fps <= 0: fps = 30
        
        # FFmpeg command
        # -f rawvideo: input format
        # -pix_fmt bgr24: input pixel format (OpenCV standard)
        # -s: resolution
        # -i -: read from stdin
        # -c:v libx264: encoding
        # -preset ultrafast: minimal CPU usage
        # -crf 23: standard quality
        # -pix_fmt yuv420p: output pixel format for compatibility
        cmd = [
            'ffmpeg', '-y',
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-s', f'{width}x{height}',
            '-pix_fmt', 'bgr24',
            '-r', str(fps),
            '-i', '-',
            '-c:v', 'libx264',
            '-preset', 'ultrafast',
            '-tune', 'zerolatency', # Good for avoiding buffering delays
            '-crf', '25', # Slightly lower quality for speed (lower is better, 28 is defaultish)
            '-pix_fmt', 'yuv420p',
            filename
        ]
        
        print(f"[{self.camera.name}] FFMPEG Rec Start: {filename} ({width}x{height})")
        
        # Open FFmpeg process
        try:
             self.process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
             print(f"[{self.camera.name}] Error starting ffmpeg: {e}")
             self.process = None

        self.start_time = time.time()
        self.current_filename = filename

    def _stop_recording(self):
        if self.process:
            if self.process.stdin:
                self.process.stdin.close()
            self.process.wait()
            print(f"[{self.camera.name}] Saved {self.current_filename}")
            self.process = None

    def start(self):
        self.running = True
        self.producer_thread = Thread(target=self.producer_loop)
        self.producer_thread.daemon = True
        self.producer_thread.start()
        
        self.consumer_thread = Thread(target=self.consumer_loop)
        self.consumer_thread.daemon = True
        self.consumer_thread.start()

    def stop(self):
        self.running = False
        if self.producer_thread:
            self.producer_thread.join()
        if self.consumer_thread:
            self.consumer_thread.join()
        self._stop_recording()

    def producer_loop(self):
        while self.running:
            frame = self.camera.read()
            if frame is not None:
                if not self.frame_queue.full():
                    self.frame_queue.put(frame)
            time.sleep(1/35.0) 
            
    def consumer_loop(self):
        self._start_recording()
        
        # Frame pacing variables
        frame_interval = 1.0 / self.camera.fps if self.camera.fps > 0 else 1.0/30.0
        frames_written = 0
        last_frame = None
        
        # We base timing on the Start of Recording
        # self.start_time is set in _start_recording
        
        while self.running or not self.frame_queue.empty():
            # 1. Get latest available frame from queue
            try:
                # Non-blocking get to not stall pacing
                if not self.frame_queue.empty():
                    frame = self.frame_queue.get_nowait()
                    last_frame = frame
                elif last_frame is None:
                    # We haven't got the first frame yet, wait a bit
                    time.sleep(0.005)
                    continue
            except queue.Empty:
                pass

            if last_frame is None:
                continue

            # 2. Check how many frames we SHOULD have written by now
            elapsed = time.time() - self.start_time
            expected_frames = int(elapsed / frame_interval)
            
            # 3. Catch up: Write duplicate frames if we are behind
            # Usually strict equality or catching up 1 frame is enough, 
            # but if we lag hard, we might write multiple. 
            # Limit catchup to avoid explosions? No, ffmpeg needs them.
            
            while frames_written < expected_frames:
                if self.process and self.process.stdin:
                    try:
                        self.process.stdin.write(last_frame.tobytes())
                        # Flush lazily? No, just write.
                    except (BrokenPipeError, OSError):
                        print(f"[{self.camera.name}] Error writing to ffmpeg (broken pipe)")
                        # Should we restart or break?
                        # break inner loop
                        break
                
                frames_written += 1
            
            # Check split
            if self.process and (elapsed > self.split_interval):
                self._stop_recording()
                self._start_recording()
                # Reset timing for new file
                frames_written = 0
                # _start_recording resets self.start_time
            
            # Sleep a tiny bit to prevent CPU spinning, but short enough to meet 30fps (33ms)
            time.sleep(0.002)
                
        self._stop_recording()
