import sqlite3

def view_vitals():
    try:
        # Connect to the SQLite database
        conn = sqlite3.connect('instance/hospital.db')
        cursor = conn.cursor()

        # Query the most recent 10 vitals
        query = '''
        SELECT patient_id, recorded_at, heart_rate, spo2, temperature, blood_pressure, is_simulated
        FROM vital_record
        ORDER BY recorded_at DESC
        LIMIT 10
        '''
        
        cursor.execute(query)
        rows = cursor.fetchall()
        
        if not rows:
            print("No vitals found in the database.")
            return

        print(f"{'Patient ID':<12} | {'Recorded At':<20} | {'HR':<5} | {'SpO2':<5} | {'Temp':<6} | {'BP':<5} | {'Simulated'}")
        print("-" * 80)
        
        for row in rows:
            pid, recorded_at, hr, spo2, temp, bp, is_simulated = row
            # Format recorded_at slightly by taking string representation subset if needed
            recorded_at_str = str(recorded_at)[:19] 
            
            hr_str = f"{hr:.1f}" if hr is not None else "N/A"
            spo2_str = f"{spo2:.1f}" if spo2 is not None else "N/A"
            temp_str = f"{temp:.1f}" if temp is not None else "N/A"
            bp_str = f"{bp:.1f}" if bp is not None else "N/A"
            
            print(f"{pid:<12} | {recorded_at_str:<20} | {hr_str:<5} | {spo2_str:<5} | {temp_str:<6} | {bp_str:<5} | {bool(is_simulated)}")

    except Exception as e:
        print(f"Error accessing database: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == '__main__':
    view_vitals()
