import cv2
import datetime
import time
import numpy as np
from threading import Thread, Lock

class CameraReader:
    def __init__(self, source_id, name="Camera"):
        self.source_id = source_id
        self.name = name
        self.cap = cv2.VideoCapture(source_id)
        
        # Set resolution to max possible, or reasonable high default
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)
        
        self.frame_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.frame_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        if self.fps <= 0:
            self.fps = 30 # Default fallback
            
        self.running = False
        self.lock = Lock()
        self.latest_frame = None
        self.thread = None

    def start(self):
        if self.cap.isOpened():
            self.running = True
            self.thread = Thread(target=self.update, args=())
            self.thread.daemon = True
            self.thread.start()
        else:
            print(f"Error: Could not open camera {self.name} (ID: {self.source_id})")

    def update(self):
        while self.running:
            ret, frame = self.cap.read()
            if ret:
                # Add timestamp to the frame immediately
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                # Put text on bottom right or top left
                cv2.putText(frame, timestamp, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 
                            1, (0, 255, 0), 2, cv2.LINE_AA)
                
                with self.lock:
                    self.latest_frame = frame
            else:
                # If reading fails, maybe try to reconnect or just wait
                time.sleep(0.1)

    def read(self):
        with self.lock:
            if self.latest_frame is not None:
                return self.latest_frame.copy()
            return None

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()
        self.cap.release()

class MockCameraReader(CameraReader):
    def __init__(self, source_id, name="MockCamera"):
        self.source_id = source_id
        self.name = name
        # HD resolution
        self.frame_width = 1280
        self.frame_height = 720
        self.fps = 30.0
        
        self.running = False
        self.lock = Lock()
        self.latest_frame = np.zeros((self.frame_height, self.frame_width, 3), dtype=np.uint8)
        self.thread = None

    def start(self):
        self.running = True
        self.thread = Thread(target=self.update, args=())
        self.thread.daemon = True
        self.thread.start()

    def update(self):
        # Generate a dummy frame with changing color or noise
        counter = 0
        while self.running:
            # Create a frame with some visual change
            fake_frame = np.zeros((self.frame_height, self.frame_width, 3), dtype=np.uint8)
            
            # Helper to make color cycle
            r = (counter % 255)
            g = ((counter * 2) % 255)
            b = ((counter * 3) % 255)
            
            cv2.rectangle(fake_frame, (100, 100), (1180, 620), (b, g, r), -1)
            
            # Put name
            cv2.putText(fake_frame, f"{self.name} - MOCK", (200, 300), 
                        cv2.FONT_HERSHEY_SIMPLEX, 2, (255, 255, 255), 4)

            # Add timestamp
            timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
            cv2.putText(fake_frame, timestamp, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 
                        1, (0, 255, 0), 2, cv2.LINE_AA)
            
            with self.lock:
                self.latest_frame = fake_frame
            
            counter += 1
            time.sleep(1.0 / self.fps)

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join()

def scan_cameras(max_search=10):
    available_cameras = []
    for i in range(max_search):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                available_cameras.append(i)
            cap.release()
    return available_cameras
