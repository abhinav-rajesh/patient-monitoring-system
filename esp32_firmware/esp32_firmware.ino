#include <Wire.h>
#include <Adafruit_MLX90614.h>
#include <DHT.h>

// Humidity Sensor (DHT) Pins
#define DHTPIN 4
#define DHTTYPE DHT11 // Change to DHT22 if you are using a DHT22 instead of DHT11
DHT dht(DHTPIN, DHTTYPE);

// Temperature Sensor (MLX90614)
Adafruit_MLX90614 mlx = Adafruit_MLX90614();

// AD8232 (ECG) Pins
const int LO_PLUS = 14;
const int LO_MINUS = 27;
const int OUTPUT_PIN = 34;

unsigned long lastTime = 0;
int beatCount = 0;
bool isHigh = false;

void setup() {
  Serial.begin(115200);
  
  // I2C for MLX90614 (SDA=21, SCL=22)
  Wire.begin(21, 22); 
  if (!mlx.begin()) {
    Serial.println("{\"error\": \"Failed to find MLX90614 sensor\"}");
  }
  
  dht.begin();
  
  pinMode(LO_PLUS, INPUT);
  pinMode(LO_MINUS, INPUT);
  pinMode(OUTPUT_PIN, INPUT);
}

void loop() {
  // --- Simple BPM Calculation from AD8232 ---
  // If leads are on, calculate peak detections
  if(digitalRead(LO_PLUS) == 0 && digitalRead(LO_MINUS) == 0) {
    int ecgValue = analogRead(OUTPUT_PIN);
    
    // Very basic peak detection logic
    // You may need to adjust the threshold (e.g. 2500) based on your AD8232 analog signal profile.
    if(ecgValue > 2500 && !isHigh){
      isHigh = true;
      beatCount++;
    }
    if(ecgValue < 2000){
      isHigh = false;
    }
  }

  // --- Send Data every 2 seconds ---
  if(millis() - lastTime > 2000) {
    // 1. Calculate BPM (Beats per 2 seconds * 30 = Beats per minute)
    float bpm = beatCount * 30.0;
    beatCount = 0; // Reset for next interval
    lastTime = millis();
    
    // 2. Read Object Temperature (Body Temp)
    float objTemp = mlx.readObjectTempC();
    if (isnan(objTemp)) { objTemp = 0.0; }
    
    // 3. Read Humidity
    float humidity = dht.readHumidity();
    if (isnan(humidity)) { humidity = 0.0; }

    // --- Format as JSON for Python Script to Consume ---
    Serial.print("{");
    Serial.print("\"temperature\": "); Serial.print(objTemp); Serial.print(", ");
    Serial.print("\"heart_rate\": "); Serial.print(bpm); Serial.print(", ");
    Serial.print("\"humidity\": "); Serial.print(humidity);
    Serial.println("}");
  }
  
  // Short delay for stability during peak detection sampling
  delay(10);
}
