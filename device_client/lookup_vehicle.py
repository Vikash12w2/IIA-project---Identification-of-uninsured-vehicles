import sys
import json
from integration_engine import build_vehicle_view, check_services


def main():
    if len(sys.argv) != 2:
        print("Usage: python lookup_vehicle.py <PLATE_NUMBER>")
        sys.exit(1)

    check_services()

    plate = sys.argv[1].upper()
    view = build_vehicle_view(plate)

    if view["times_captured"] == 0 and not view["registered"]:
        print(f"\nNo record found anywhere for plate '{plate}'.")
        return

    print()
    print(json.dumps(view, indent=2))


if __name__ == "__main__":
    main()
