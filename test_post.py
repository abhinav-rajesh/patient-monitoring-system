import time
import requests

url = "http://127.0.0.1:5000/api/sensor_data"
data = {
    "patient_id": "P001",
    "temperature": 37.1,
    "heart_rate": 85.0,
    "humidity": 65.0 # => SpO2 ~ 96.5%
}

for _ in range(15):
    try:
        r = requests.post(url, json=data)
        print("Sent info:", r.json())
        data["heart_rate"] += 1.0 # gradual increase to see change
    except Exception as e:
        print("Error:", e)
    time.sleep(2)
