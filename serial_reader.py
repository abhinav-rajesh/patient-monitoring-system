import serial
import time
import requests
import json
import argparse

# Default Configuration
API_ENDPOINT = 'http://127.0.0.1:5000/api/sensor_data'

def main():
    parser = argparse.ArgumentParser(description="Read ESP32 vital sensors and send to MedWatch server")
    parser.add_argument('--port', type=str, default='COM3', help='COM port for ESP32 (e.g., COM3, /dev/ttyUSB0)')
    parser.add_argument('--baud', type=int, default=115200, help='Baud rate')
    args = parser.parse_args()

    try:
        ser = serial.Serial(args.port, args.baud, timeout=2)
        print(f"✅ Connected to ESP32 on {args.port} at {args.baud} baud.")
    except Exception as e:
        print(f"❌ Failed to connect to {args.port}: {e}")
        print("Please check your COM port in the Device Manager and make sure the ESP32 is plugged in.")
        print("Usage example: python serial_reader.py --port COM4")
        return

    print(f"📡 Forwarding data to {API_ENDPOINT}...\n")

    while True:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                
                # We expect the ESP32 to print a JSON string
                if line.startswith('{') and line.endswith('}'):
                    try:
                        data = json.loads(line)
                        data["patient_id"] = "P001" # Default to attaching real data to Patient A
                        
                        # Send to Flask app
                        response = requests.post(API_ENDPOINT, json=data)
                        if response.status_code == 200:
                            print(f"[OK] Sent: Temp={data.get('temperature')}°C, HR={data.get('heart_rate')} BPM, Humidity={data.get('humidity')}%")
                        else:
                            print(f"[ERROR] Server returned {response.status_code}: {response.text}")
                    except json.JSONDecodeError:
                        print(f"Malformed JSON: {line}")
            time.sleep(0.05)
        except KeyboardInterrupt:
            print("\nExiting...")
            break
        except Exception as e:
            print(f"Error reading serial or sending request: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
