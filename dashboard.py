import sys
import cv2
import time
import serial
import threading
import platform
import os
import ssl
import paho.mqtt.client as mqtt
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QLabel, 
                               QVBoxLayout, QHBoxLayout, QPushButton, QFrame, QGridLayout)
from PySide6.QtCore import Qt, QThread, Slot, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap, QFont
from detector import VideoReader, TrafficDetector 

# --- MQTT CONFIGURATION ---
MQTT_BROKER = "234f69c003d543be8297e4a25e38db6b.s1.eu.hivemq.cloud"
MQTT_PORT = 8883
MQTT_USER = "al_randolph_villegas"
MQTT_PASS = "123456789aA"
TOPIC_CONTROL = "traffic/hardware/control"

class MqttController:
    """Handles secure communication with HiveMQ Cloud"""
    def __init__(self):
        # Use Version 2 of the Callback API to avoid deprecation warnings
        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.username_pw_set(MQTT_USER, MQTT_PASS)
        
        # Enable TLS for Port 8883
        self.client.tls_set(tls_version=ssl.PROTOCOL_TLS_CLIENT)
        
        try:
            self.client.connect(MQTT_BROKER, MQTT_PORT, 60)
            self.client.loop_start()
            print("MQTT: Connected to HiveMQ Cloud")
        except Exception as e:
            print(f"MQTT Connection Error: {e}")

    def send_hardware_state(self, bit_string, duration):
        """Sends 'ABC,duration' (e.g., '001,3')"""
        payload = f"{bit_string},{duration}"
        self.client.publish(TOPIC_CONTROL, payload)
        print(f"MQTT SENT -> State: {bit_string} | Duration: {duration}s")

# Global MQTT instance
mqtt_hardware = MqttController()

class SerialThread(QThread):
    siren_signal = Signal()
    def run(self):
        port = 'COM3' if platform.system() == "Windows" else '/dev/ttyUSB0'
        try:
            ser = serial.Serial(port, 115200, timeout=0.1)
            time.sleep(2) 
            while True:
                if ser.in_waiting > 0:
                    line = ser.readline().decode('utf-8', errors='ignore').strip()
                    if "<SIREN>" in line:
                        self.siren_signal.emit()
        except: pass

class Worker(QThread):
    def __init__(self, detector):
        super().__init__()
        self.detector = detector
        self.cam_a = VideoReader(0).start()
        self.cam_b = VideoReader(1).start() 
        self.start_time = time.time()
        self.current_duration = 5 
        self.last_valid_count = 0
        self.last_sent_bits = "" # Track state to prevent duplicate MQTT messages

    def update_hardware(self):
        """Maps Traffic Logic to the 3-bit Binary Input for the Logic Circuit"""
        # Default State: 010 (Emergency / Detecting / All Red)
        bit_string = "010"
        
        if self.detector.emergency_mode:
            bit_string = "010"
        elif self.detector.light_color == "DETECTING":
            bit_string = "010"
        elif self.detector.active_street == "Street A":
            if self.detector.light_color == "GREEN": bit_string = "000"
            elif self.detector.light_color == "YELLOW": bit_string = "001"
        elif self.detector.active_street == "Street B":
            if self.detector.light_color == "GREEN": bit_string = "011"
            elif self.detector.light_color == "YELLOW": bit_string = "100"

        # Only send via MQTT if the binary state has changed
        if bit_string != self.last_sent_bits:
            mqtt_hardware.send_hardware_state(bit_string, int(self.current_duration))
            self.last_sent_bits = bit_string

    def run(self):
        while self.detector.running:
            self.update_hardware()
            if not self.detector.emergency_mode:
                elapsed = time.time() - self.start_time
                time_left = max(0, int(self.current_duration - elapsed))
                ret_a, frame_a = self.cam_a.read()
                ret_b, frame_b = self.cam_b.read()

                if ret_a and frame_a is not None:
                    c_a = self.detector.process_street(frame_a, "Street A", time_left)
                    if self.detector.active_street == "Street A" and self.detector.is_counting:
                        self.last_valid_count = c_a
                if ret_b and frame_b is not None:
                    c_b = self.detector.process_street(frame_b, "Street B", time_left)
                    if self.detector.active_street == "Street B" and self.detector.is_counting:
                        self.last_valid_count = c_b

                if elapsed >= self.current_duration:
                    self.handle_transition()
            else:
                ret_a, frame_a = self.cam_a.read()
                ret_b, frame_b = self.cam_b.read()
                if ret_a and frame_a is not None: self.detector.process_street(frame_a, "Street A", 0)
                if ret_b and frame_b is not None: self.detector.process_street(frame_b, "Street B", 0)
            self.msleep(60) 

    def handle_transition(self):
        if self.detector.is_counting:
            duration = self.detector.get_duration(self.last_valid_count)
            if duration > 0:
                self.start_time = time.time()
                self.detector.is_counting = False
                self.detector.light_color = "GREEN"
                self.current_duration = duration
            else: self.swap_lanes()
        elif self.detector.light_color == "GREEN":
            self.start_time = time.time()
            self.detector.light_color = "YELLOW"
            self.current_duration = 3
        else: self.swap_lanes()

    def swap_lanes(self):
        self.start_time = time.time()
        self.detector.is_counting = True
        self.detector.light_color = "DETECTING"
        self.current_duration = 5 
        self.last_valid_count = 0
        self.detector.active_street = "Street B" if self.detector.active_street == "Street A" else "Street A"

class Dashboard(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Traffic Monitor - ESP32 MQTT Control")
        self.resize(1280, 720)
        self.setStyleSheet("""
            QMainWindow { background-color: #050505; } 
            QLabel { color: #ffffff; font-family: 'Courier New'; } 
            QFrame#Card { background-color: #1a1a1a; border: 2px solid #333; border-radius: 10px; }
        """)
        
        central = QWidget()
        self.setCentralWidget(central)
        main_lay = QVBoxLayout(central)
        
        self.title_label = QLabel("CCTV TRAFFIC MONITORING STATION")
        self.title_label.setFont(QFont("Courier New", 20, QFont.Bold))
        header = QHBoxLayout()
        header.addWidget(self.title_label)
        header.addStretch()
        
        exit_btn = QPushButton("EXIT")
        exit_btn.setStyleSheet("background: #d32f2f; color: white; border-radius: 5px; padding: 10px; font-weight: bold;")
        exit_btn.clicked.connect(self.close)
        header.addWidget(exit_btn)
        main_lay.addLayout(header)

        grid = QGridLayout()
        grid.setSpacing(40)
        self.view_a = self.create_cam_card("STREET A")
        self.view_b = self.create_cam_card("STREET B")
        grid.addWidget(self.view_a['card'], 0, 0, alignment=Qt.AlignCenter)
        grid.addWidget(self.view_b['card'], 0, 1, alignment=Qt.AlignCenter)
        main_lay.addLayout(grid)
        
        self.detector = TrafficDetector()
        self.detector.frame_ready.connect(self.update_ui)
        self.worker = Worker(self.detector)
        self.worker.start()

        self.last_siren_time = 0
        self.watchdog_timer = QTimer()
        self.watchdog_timer.timeout.connect(self.check_emergency_timeout)
        self.watchdog_timer.start(500)

        self.serial_thread = SerialThread()
        self.serial_thread.siren_signal.connect(self.activate_emergency)
        self.serial_thread.start()

    def create_cam_card(self, title):
        card = QFrame()
        card.setObjectName("Card")
        l = QVBoxLayout(card)
        l.setContentsMargins(15, 15, 15, 15)
        
        tit = QLabel(title)
        tit.setAlignment(Qt.AlignCenter)
        tit.setFont(QFont("Courier New", 24, QFont.Bold))
        
        v = QLabel()
        v.setFixedSize(640, 480)
        v.setStyleSheet("background: black; border: 1px solid #555;")
        
        info = QHBoxLayout()
        c_lbl = QLabel("DETECTED: 0")
        c_lbl.setFont(QFont("Courier New", 18, QFont.Bold))
        c_lbl.setStyleSheet("color: #2979ff;")
        
        t_lbl = QLabel("00")
        t_lbl.setAlignment(Qt.AlignCenter)
        t_lbl.setFont(QFont("Courier New", 50, QFont.Bold))
        
        info.addWidget(c_lbl)
        info.addStretch()
        info.addWidget(t_lbl)
        
        s_lbl = QLabel("STATUS: STANDBY")
        s_lbl.setAlignment(Qt.AlignCenter)
        s_lbl.setFont(QFont("Courier New", 20, QFont.Bold))
        
        l.addWidget(tit)
        l.addWidget(v)
        l.addLayout(info)
        l.addWidget(s_lbl)
        return {'card': card, 'video': v, 'timer': t_lbl, 'status': s_lbl, 'count': c_lbl}

    @Slot()
    def activate_emergency(self):
        self.last_siren_time = time.time()
        if not self.detector.emergency_mode:
            self.detector.emergency_mode = True
            self.title_label.setText("!!! EMERGENCY VEHICLE DETECTED - ALL RED !!!")
            self.title_label.setStyleSheet("color: #ff1744; font-weight: bold;")

    def check_emergency_timeout(self):
        if self.detector.emergency_mode and time.time() - self.last_siren_time > 1.5:
            self.deactivate_emergency()

    def deactivate_emergency(self):
        self.detector.emergency_mode = False
        self.title_label.setText("CCTV TRAFFIC MONITORING STATION")
        self.title_label.setStyleSheet("color: white;")
        self.worker.start_time = time.time()

    @Slot(object, int, str, str, int, bool)
    def update_ui(self, frame, count, street, status, time_left, is_emergency):
        if frame is None: return
        try:
            img = frame.copy()
            rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
            q_img = QImage(rgb.data, 640, 480, 640 * 3, QImage.Format_RGB888)
            pix = QPixmap.fromImage(q_img).copy()
            
            v = self.view_a if street == "Street A" else self.view_b
            v['video'].setPixmap(pix)
            
            if is_emergency:
                v['status'].setText("!!! EMERGENCY !!!")
                v['status'].setStyleSheet("color: #ff1744;")
                v['timer'].setText("--")
                v['count'].setText("DETECTED: --")
            elif street == self.detector.active_street:
                v['timer'].setText(f"{time_left:02d}")
                v['count'].setText(f"DETECTED: {count}")
                color = "#00ff41" if status == "GREEN" else "#ffea00" if status == "YELLOW" else "#ff1744"
                v['status'].setText(f"STATUS: {status}")
                v['status'].setStyleSheet(f"color: {color};")
                v['timer'].setStyleSheet(f"color: {color};")
                
                other = self.view_b if street == "Street A" else self.view_a
                other['status'].setText("STATUS: RED")
                other['status'].setStyleSheet("color: #ff1744;")
                other['timer'].setText("--")
        except: pass

    def closeEvent(self, event):
        self.detector.running = False
        self.worker.quit()
        self.worker.wait()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = Dashboard()
    window.show()
    sys.exit(app.exec())