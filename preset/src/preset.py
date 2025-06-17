import json
import time

class DriverPreset:
    def __init__(self, seat_position: int = 50, mirror_angle: int = 0, climate_temp: float = 22.0):
        self.seat_position = seat_position
        self.mirror_angle = mirror_angle
        self.climate_temp = climate_temp

    def to_dict(self):
        return {
            'seat_position': self.seat_position,
            'mirror_angle': self.mirror_angle,
            'climate_temp': self.climate_temp
        }

    @staticmethod
    def from_dict(data):
        return DriverPreset(
            seat_position=data['seat_position'],
            mirror_angle=data['mirror_angle'],
            climate_temp=data['climate_temp']
        )

    def __str__(self):
        return (f"[AosEdge] Current preset: \n"
                f"  Seat Position: {self.seat_position}/100\n"
                f"  Mirror Angle: {self.mirror_angle}°\n"
                f"  Climate Temperature: {self.climate_temp}°C")


def save_preset_to_file(preset: DriverPreset, filename="state.dat"):
    with open(filename, "w") as file:
        print(f"\nPreset save to {filename}")
        json.dump(preset.to_dict(), file, indent=4)
    

def load_preset_from_file(filename="state.dat"):
    try:
        with open(filename, "r") as file:
            raw_text = file.read()
            data = json.loads(raw_text)
            preset = DriverPreset.from_dict(data)
            print(f"\nPreset loaded from {filename}")
            return preset
    except FileNotFoundError:
        print(f"\nFile {filename} not found. Loading default preset.")
        return DriverPreset()

def main():
    preset = load_preset_from_file("state.dat")
    
    preset.seat_position += 10
    preset.mirror_angle += 10
    preset.climate_temp += 1.0
    
    save_preset_to_file(preset)
    print(f"\n{preset}")

    while True:
        print(f"\n{preset}")
        time.sleep(5)

if __name__ == "__main__":
    main()
