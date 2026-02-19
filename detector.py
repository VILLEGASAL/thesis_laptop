import cv2
import threading
from ultralytics import YOLO
from PySide6.QtCore import QObject, Signal

class VideoReader:
    def __init__(self, src):
        self.cap = cv2.VideoCapture(src)
        # Set resolution for better laptop performance
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.grabbed, self.frame = self.cap.read()
        self.started = False
        self.read_lock = threading.Lock()

    def start(self):
        if self.started: return self
        self.started = True
        self.thread = threading.Thread(target=self.update, args=())
        self.thread.daemon = True
        self.thread.start()
        return self

    def update(self):
        while self.started:
            grabbed, frame = self.cap.read()
            if grabbed and frame is not None:
                with self.read_lock:
                    self.grabbed = grabbed
                    self.frame = frame.copy()
            else:
                self.grabbed = False

    def read(self):
        with self.read_lock:
            frame = self.frame.copy() if self.frame is not None else None
            grabbed = self.grabbed
        return grabbed, frame

    def stop(self):
        self.started = False
        if self.cap.isOpened():
            self.cap.release()

class TrafficDetector(QObject):
    frame_ready = Signal(object, int, str, str, int, bool) 

    def __init__(self):
        super().__init__()
        # Laptop fallback for model loading
        try:
            self.model = YOLO("./yolo11n_ncnn_model", task="detect")
        except:
            self.model = YOLO("yolo11n.pt", task="detect")
            
        self.ZONE = [100, 80, 540, 400] 
        self.running = True
        self.active_street = "Street A"
        self.is_counting = True
        self.light_color = "DETECTING"
        self.vehicle_classes = [2, 3, 5, 7]
        self.emergency_mode = False

    def get_duration(self, count):
        if count == 1: return 5
        elif count == 2: return 10
        elif count >= 3: return 15
        return 0 

    def process_street(self, frame, street_name, time_left):
        if frame is None: return 0
        vehicle_count = 0
        display_color = "RED" 
        working_frame = frame.copy()

        if street_name == self.active_street:
            display_color = "EMERGENCY" if self.emergency_mode else self.light_color
            if self.is_counting and not self.emergency_mode:
                results = self.model(working_frame, imgsz=320, verbose=False, stream=True)
                cv2.rectangle(working_frame, (self.ZONE[0], self.ZONE[1]), (self.ZONE[2], self.ZONE[3]), (255, 255, 0), 2)
                for r in results:
                    for box in r.boxes:
                        if int(box.cls[0]) in self.vehicle_classes:
                            x1, y1, x2, y2 = box.xyxy[0].tolist()
                            if (x1 >= self.ZONE[0] and y1 >= self.ZONE[1] and x2 <= self.ZONE[2] and y2 <= self.ZONE[3]):
                                vehicle_count += 1
                                cv2.rectangle(working_frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
            
            rect_color = (0, 0, 255) if display_color in ["RED", "EMERGENCY"] else (0, 255, 0) if display_color == "GREEN" else (0, 255, 255)
            cv2.rectangle(working_frame, (self.ZONE[0], self.ZONE[1]), (self.ZONE[2], self.ZONE[3]), rect_color, 4 if self.emergency_mode else 2)

        self.frame_ready.emit(working_frame.copy(), vehicle_count, street_name, display_color, time_left, self.emergency_mode)
        return vehicle_count