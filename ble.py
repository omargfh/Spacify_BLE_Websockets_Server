import simplepyble
print(f"Running on {simplepyble.get_operating_system()}")
adapters = simplepyble.Adapter.get_adapters()

if len(adapters) == 0:
    print("No adapters found")

for adapter in adapters:
    print(f"Adapter: {adapter.identifier()} [{adapter.address()}]")

if len(adapters) != 1:
    print("Please connect only one adapter")
    exit(1)

adapter = adapters[0]
adapter.set_callback_on_scan_start(lambda: print("Scan started."))
adapter.set_callback_on_scan_stop(lambda: print("Scan complete."))
ble_log = lambda peripheral: print(f"Found {peripheral.identifier()} [{peripheral.address()}]")
adapter.set_callback_on_scan_found(ble_log)

def ble_scan(adapter, device_name, timeout=5000):
    device = None
    def on_receive(scan_entry):
        nonlocal device
        if scan_entry.identifier() == device_name and device == None:
            device = scan_entry
    adapter.set_callback_on_scan_found(on_receive)
    adapter.scan_for(timeout)
    adapter.set_callback_on_scan_found(ble_log)
    return device

external = None
DEFAULT_ATTEMPTS = 3
for i in range(DEFAULT_ATTEMPTS):
    external = ble_scan(adapter, "ELK-BLEDOM0E")
    if external is not None:
        break
device = external
if not device:
    print("Device not found")
    exit(1)

WRITE_CHARACTERISTIC_UUIDS = "0000fff3-0000-1000-8000-00805f9b34fb"
READ_CHARACTERISTIC_UUIDS = "0000fff4-0000-1000-8000-00805f9b34fb"
TURN_ON_CMD = bytes([0x7e, 0x00, 0x04, 0xf0, 0x00, 0x01, 0xff, 0x00, 0xef])
TURN_OFF_CMD = bytes([0x7e, 0x00, 0x04, 0x00, 0x00, 0x00, 0xff, 0x00, 0xef])
MIN_COLOR_TEMP_K = 2700
MAX_COLOR_TEMPS_K = 6500
BLEAK_BACKOFF_TIME = 0.25

device.connect()
print(f"Connected to {device.identifier()} [{device.address()}]")

services = device.services()
for service in services:
    print(f"Service: {service.uuid()}")
if len(services) != 1:
    print("Please connect only one service")
    exit(1)
service = services[0]
suid = service.uuid()

import datetime
from typing import Tuple
class LEDProxy:
    def __init__(self, device, suid, write_characteristic, read_characteristic, min_color_temp_k=MIN_COLOR_TEMP_K, max_color_temp_k=MAX_COLOR_TEMPS_K):
        self.device = device
        self.suid = suid
        self.write_characteristic = write_characteristic
        self.read_characteristic = read_characteristic

        self._min_color_temp_k = min_color_temp_k
        self._max_color_temp_k = max_color_temp_k

        self._rgb_color = None
        self._brightness = None
        self._effect = None
        self._effect_speed = None
        self._color_temp_kelvin = None


    def _write(self, data):
        if type(data) == list:
            data = bytes(data)
        self.device.write_command(self.suid, self.write_characteristic, data)

    def _read(self):
        return self.device.read(self.suid, self.read_characteristic)

    def turn_on(self):
        self._write(TURN_ON_CMD)

    def turn_off(self):
        self._write(TURN_OFF_CMD)

    def sync_time(self):
        date=datetime.date.today()
        year, week_num, day_of_week = date.isocalendar()
        self._write([0x7e, 0x00, 0x83, datetime.datetime.now().strftime('%H'), datetime.datetime.now().strftime('%M'), datetime.datetime.now().strftime('%S'), day_of_week, 0x00, 0xef])

    def set_color(self, rgb: Tuple[int, int, int]):
        r, g, b = rgb
        self._write([0x7e, 0x00, 0x05, 0x03, r, g, b, 0x00, 0xef])
        self._rgb_color = rgb

    def set_white(self, intensity: int):
        self._write([0x7e, 0x00, 0x01, int(intensity*100/255), 0x00, 0x00, 0x00, 0x00, 0xef])
        self._brightness = intensity


    def set_brightness(self, intensity: int):
        self._write([0x7e, 0x04, 0x01, int(intensity*100/255), 0xff, 0x00, 0xff, 0x00, 0xef])
        self._brightness = intensity


    def set_effect_speed(self, value: int):
        self._write([0x7e, 0x00, 0x02, value, 0x00, 0x00, 0x00, 0x00, 0xef])
        self._effect_speed = value


    def set_effect(self, value: int):
        self._write([0x7e, 0x00, 0x03, value, 0x03, 0x00, 0x00, 0x00, 0xef])
        self._effect = value

import threading
import time
from enum import Enum
class FRAME_SIGNAL(Enum):
    CONTINUE = 0
    STOP = 1

class LEDAnimator:
    def __init__(self, led: LEDProxy, fps: int = 30, on_frame=None):
        self.led = led
        self.fps = fps
        self._running = False
        self._thread = None
        self._frame = on_frame

        self.frames = 0

    def animate(self):
        if self._running:
            print("Cannot run two animations at the same time")
            return
        self._running = True
        self._thread = threading.Thread(target=self._animate)
        self._thread.start()

    def _animate(self):
        time = time.time()
        while self._running:
            if self._frame(time - time.time(), self.frames) == FRAME_SIGNAL.STOP:
                self._running = False
            time = time.time()
            time.sleep(1/self.fps)
            self.frames += 1
        self.frames = 0
        self._running = False



import time

lproxy = LEDProxy(device, suid, WRITE_CHARACTERISTIC_UUIDS, READ_CHARACTERISTIC_UUIDS, MIN_COLOR_TEMP_K, MAX_COLOR_TEMPS_K)
lproxy.turn_on()

from websockets.server import serve
import asyncio

HOSTNAME = "127.0.0.1"
PORT = 9024
COMMANDS= {"a": (2, [float, float]), "c": (3, [int, int, int])}
BRIGHTNESS = 1.0

lerp = lambda a, b, t: a + (b - a) * t

lproxy.set_color((255, 0, 0))
lproxy.set_brightness(255)

threads = []
last_brightness = 0.5
print = lambda x: None

async def handler(websocket):
    async for message in websocket:
        try:
            print(f"Received message: {message}")
            parts = message.strip().split(' ')
            command = parts[0]
            if command.lower() not in COMMANDS:
                print(f"[Warn] Unknown command {command}")
                continue

            arg_count, types = COMMANDS[command.lower()]
            args = []
            for i in range(1, arg_count + 1):
                args.append(types[i-1](parts[i]))

            if command.lower() == "a":
                # print(f"Executing {command} with args {args}")
                amplitude = args[0]
                timeFactor = args[1]

                global last_brightness
                last_brightness = lerp(last_brightness, min(1.0, max(0.0, 0.3 * amplitude ** 2 - 0.3)), pow(2, timeFactor) / 5)
                print(f"Setting brightness to {last_brightness * 254}")
                def a():
                    lproxy.set_brightness(int(last_brightness * 254))
                threads.append(threading.Thread(target=a))
                threads[-1].start()

            if command.lower() == "c":
                print(f"Executing {command} with args {args}")
                r, g, b = args
                threads.append(threading.Thread(target=lambda: lproxy.set_color((r, g, b))))
                threads[-1].start()
            # print(f"Handled message: {message}")
        except Exception as e:
            print(f"Error: {e}")


async def main():
    async with serve(handler, HOSTNAME, PORT):
        await asyncio.Future()  # run forever

asyncio.run(main())