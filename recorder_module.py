import cv2
import os
import datetime
import time
from threading import Thread
import queue

class VideoRecorder:
    def __init__(self, camera_reader, storage_path, split_interval=300):
        self.camera = camera_reader
        self.base_storage_path = storage_path
        self.split_interval = split_interval
        
        self.running = False
        self.out = None
        self.current_filename = None
        self.start_time = None
        self.thread = None
        
        # Queue for frames to decouple capture from I/O blocking
        # Max size to prevent memory overflow if disk is too slow
        self.frame_queue = queue.Queue(maxsize=150) 
        
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
            
        # Changed to .mp4 for better compatibility? Or .avi with MJPG
        return os.path.join(cam_dir, f"{time_str}.avi")

    def _start_recording(self):
        filename = self._get_output_filepath()
        # MJPG is much faster to encode on Pi CPU than XVID/H264 (which are heavy without HW accel)
        fourcc = cv2.VideoWriter_fourcc(*'MJPG') 
        
        width = self.camera.frame_width
        height = self.camera.frame_height
        fps = self.camera.fps
        
        print(f"[{self.camera.name}] Rec Start: {filename} ({width}x{height} @ {fps}fps)")
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
        # Producer thread (reads from camera, puts to queue)
        self.producer_thread = Thread(target=self.producer_loop)
        self.producer_thread.daemon = True
        self.producer_thread.start()
        
        # Consumer thread (reads from queue, writes to disk)
        self.consumer_thread = Thread(target=self.consumer_loop)
        self.consumer_thread.daemon = True
        self.consumer_thread.start()

    def stop(self):
        self.running = False
        if self.producer_thread:
            self.producer_thread.join()
        # Wait for queue to empty? Or just stop?
        # Better to wait a bit
        if self.consumer_thread:
            self.consumer_thread.join()
        self._stop_recording()

    def producer_loop(self):
        """Standard loop to grab frames from camera and push to queue"""
        while self.running:
            frame = self.camera.read()
            if frame is not None:
                if not self.frame_queue.full():
                    self.frame_queue.put(frame)
                else:
                    # Drop frame if queue full to keep live current
                    pass
            
            # Control capture rate? 
            # The camera.read() is non-blocking in our implementation (returns latest),
            # so we're polling. We should limit to FPS.
            time.sleep(1/35.0) # slightly faster than 30 to catch duplicates?
            # Actually our camera classes have their own FPS loop.
            # But duplicate frames in queue is OK, VideoWriter expects constant FPS stream.
            
    def consumer_loop(self):
        """Write frames to disk"""
        self._start_recording()
        
        while self.running or not self.frame_queue.empty():
            if not self.frame_queue.empty():
                try:
                    frame = self.frame_queue.get(timeout=1)
                    if self.out:
                        self.out.write(frame)
                    
                    # Check split
                    if self.out and (time.time() - self.start_time > self.split_interval):
                        self._stop_recording()
                        self._start_recording()
                except queue.Empty:
                    continue
            else:
                time.sleep(0.01)
                
        self._stop_recording()
