import requests
import config


SERVICES = {
    "Device 1 - ANPR (traffic camera)": f"{config.ANPR_SERVICE}/health",
    "Device 2 - Insurance": f"{config.INSURANCE_SERVICE}/health",
    "Device 3 - RTO (registration)": f"{config.RTO_SERVICE}/health",
    "Device 4 - Theft/Shredding": f"{config.THEFT_SERVICE}/health",
    "Device 5 - MoT": f"{config.MOT_SERVICE}/health",
}


def main():
    print("=" * 70)
    print("CONNECTIVITY CHECK - client device pinging all 5 remote services")
    print("=" * 70)

    all_ok = True

    for name, url in SERVICES.items():
        try:
            resp = requests.get(url, timeout=3)
            resp.raise_for_status()
            data = resp.json()
            print(f"  [ OK ]  {name:35s} -> {url}   ({data})")

        except requests.exceptions.ConnectionError:
            print(
                f"  [FAIL]  {name:35s} -> {url}   "
                "(connection refused - service not running or wrong IP)"
            )
            all_ok = False

        except requests.exceptions.Timeout:
            print(
                f"  [FAIL]  {name:35s} -> {url}   "
                "(timed out - check firewall / network)"
            )
            all_ok = False

        except Exception as e:
            print(f"  [FAIL]  {name:35s} -> {url}   ({e})")
            all_ok = False

    print("=" * 70)

    if all_ok:
        print(
            "All 5 devices reachable. You're good to run "
            "integration_engine.py or lookup_vehicle.py."
        )
    else:
        print(
            "One or more devices unreachable. Fix the [FAIL] rows above "
            "before continuing:"
        )
        print("  1. Is the *_service.py actually running on that machine?")
        print("  2. Is the IP address in config.py correct for that machine?")
        print("  3. Are all machines on the same WiFi/hotspot network?")
        print("  4. Did a firewall prompt appear on the source machine that needs 'Allow'?")

    print("=" * 70)


if __name__ == "__main__":
    main()
