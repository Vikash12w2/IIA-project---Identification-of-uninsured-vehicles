# Addresses of every device. Each source device fetches the others' data for its automatic data cleansing,
# and ANPR sends its reports to MoT. Keep the same values as device_client/config.py, and copy this file next
# to every service when the devices run on separate machines.
PEERS = {
    "ANPR": "http://192.168.45.115:5001",
    "Insurance": "http://192.168.52.125:5002",
    "RTO": "http://192.168.45.115:5003",
    "Theft/Shredding": "http://192.168.52.125:5004",
    "MoT": "http://192.168.52.125:5005",
}
