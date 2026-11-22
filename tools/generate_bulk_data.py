"""Bulk data for the five device databases: about 10,000 records in each database's busy tables.

The data is consistent across the devices: one fleet of vehicles is registered in RTO, insured in Insurance, seen by
ANPR cameras, and partly stolen or scrapped in Theft/Shredding. It carries the kind of noise the Phase 2 cleansing
is there to find (misread plates, double entries, re-entered records, Active policies that have ended, sightings
whose make contradicts the registration). MoT reports are not generated: ANPR sends them when it cleans.

After inserting, the script runs every device's cleansing here, the devices reading each other's data directly:
the cleaned tables are built, the plate dictionaries learn the plates and the misreads that were corrected, and
ANPR's reports reach MoT, all before any service starts.

    python tools/generate_bulk_data.py                      default sizes (below)
    python tools/generate_bulk_data.py --scale 2            twice as many of everything
    python tools/generate_bulk_data.py --set theft_report=10000 --set shredding_record=10000
    python tools/generate_bulk_data.py --clean-only         only clean what is already there (no new records)
    python tools/generate_bulk_data.py --no-clean           insert only; the services clean at their next start
    python tools/generate_bulk_data.py --licences-only      only give every registration without one its owner's
                                                            driving licence (date of birth, for owner-age queries)

Every database is copied to backups/<date-time>/ first. Records are only added (nothing is deleted or changed), and
each device's cleansing is reset so that its next start cleans all records once. Stop the services before running it.
"""
import argparse
import importlib.util
import os
import random
import re
import shutil
import sqlite3
import string
import sys
from datetime import date, datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "device_rto"))
import plate_rules as rules  # noqa: E402

COUNTS = {"registration": 12000, "ownership_transfer": 5000, "puc_certificate": 10000,
          "policy": 10000, "claim": 4000, "premium_payment": 12000,
          "vehicle_capture": 10000, "capture_violation": 3000, "camera_maintenance": 2000,
          "theft_report": 1500, "shredding_record": 1000}

SERVICES = [("ANPR", "device_anpr", "anpr_service"), ("Insurance", "device_insurance", "insurance_service"),
            ("RTO", "device_rto", "rto_service"), ("Theft/Shredding", "device_theft", "theft_service"),
            ("MoT", "device_mot", "mot_service")]

# state code -> (state, [(district, city, RTO office, areas)])
STATES = {
    "MH": ("Maharashtra", [("01", "Mumbai", "RTO Mumbai Central", ["Tardeo", "Worli", "Byculla"]),
                           ("02", "Mumbai", "RTO Andheri", ["Andheri", "Juhu", "Versova"]),
                           ("04", "Thane", "RTO Thane", ["Naupada", "Kopri", "Majiwada"]),
                           ("12", "Pune", "RTO Pune", ["Kothrud", "Shivaji Nagar", "Hadapsar", "Baner"]),
                           ("14", "Pune", "RTO Pimpri-Chinchwad", ["Akurdi", "Nigdi", "Wakad"]),
                           ("15", "Nashik", "RTO Nashik", ["Panchavati", "Satpur"]),
                           ("31", "Nagpur", "RTO Nagpur", ["Sitabuldi", "Dharampeth"])]),
    "DL": ("Delhi", [("3", "New Delhi", "Zonal Office Sheikh Sarai", ["Saket", "Malviya Nagar"]),
                     ("8", "New Delhi", "Zonal Office Wazirpur", ["Karol Bagh", "Ashok Vihar"]),
                     ("9", "New Delhi", "Zonal Office Janakpuri", ["Janakpuri", "Dwarka"])]),
    "KA": ("Karnataka", [("01", "Bengaluru", "RTO Bengaluru Central", ["Shivajinagar", "Richmond Town"]),
                         ("03", "Bengaluru", "RTO Bengaluru East", ["Indiranagar", "Whitefield"]),
                         ("05", "Bengaluru", "RTO Bengaluru South", ["Jayanagar", "Koramangala"]),
                         ("09", "Mysuru", "RTO Mysuru", ["Vijayanagar", "Kuvempunagar"])]),
    "TN": ("Tamil Nadu", [("01", "Chennai", "RTO Chennai Central", ["Egmore", "Chetpet"]),
                          ("09", "Chennai", "RTO Chennai West", ["T. Nagar", "K.K. Nagar"]),
                          ("37", "Coimbatore", "RTO Coimbatore South", ["RS Puram", "Gandhipuram"]),
                          ("58", "Madurai", "RTO Madurai South", ["Anna Nagar", "KK Nagar"])]),
    "GJ": ("Gujarat", [("01", "Ahmedabad", "RTO Ahmedabad", ["Naroda", "Navrangpura", "Thaltej"]),
                       ("05", "Surat", "RTO Surat", ["Adajan", "Varachha"]),
                       ("06", "Vadodara", "RTO Vadodara", ["Alkapuri", "Manjalpur"])]),
    "RJ": ("Rajasthan", [("14", "Jaipur", "RTO Jaipur", ["Malviya Nagar", "Vaishali Nagar"]),
                         ("19", "Jodhpur", "RTO Jodhpur", ["Sardarpura", "Ratanada"])]),
    "UP": ("Uttar Pradesh", [("16", "Noida", "ARTO Noida", ["Sector 18", "Sector 62"]),
                             ("32", "Lucknow", "RTO Lucknow", ["Hazratganj", "Gomti Nagar"]),
                             ("78", "Kanpur", "RTO Kanpur", ["Civil Lines", "Kakadeo"])]),
    "PB": ("Punjab", [("10", "Ludhiana", "RTO Ludhiana", ["Sarabha Nagar", "Model Town"]),
                      ("02", "Amritsar", "RTO Amritsar", ["Ranjit Avenue", "Lawrence Road"])]),
    "WB": ("West Bengal", [("20", "Kolkata", "PVD Kolkata South", ["Ballygunge", "Behala"]),
                           ("02", "Kolkata", "PVD Kolkata North", ["Shyambazar", "Dum Dum"])]),
    "KL": ("Kerala", [("07", "Kochi", "RTO Ernakulam", ["Kadavanthra", "Edappally"]),
                      ("01", "Thiruvananthapuram", "RTO Thiruvananthapuram", ["Kowdiar", "Pattom"])]),
    "HR": ("Haryana", [("26", "Gurugram", "RTA Gurugram", ["Sector 54", "DLF Phase 3"]),
                       ("51", "Faridabad", "RTA Faridabad", ["Sector 15", "NIT"])]),
    "TS": ("Telangana", [("09", "Hyderabad", "RTA Hyderabad Central", ["Banjara Hills", "Ameerpet"]),
                         ("07", "Hyderabad", "RTA Rangareddy", ["Gachibowli", "Kondapur"])]),
}
ROADS = {"Mumbai": ["Western Express Highway", "Marine Drive", "SV Road", "LBS Marg"],
         "Thane": ["Ghodbunder Road", "Eastern Express Highway"], "Pune": ["NH48 Toll Plaza", "Katraj Chowk",
         "Shivaji Nagar", "FC Road", "University Road", "Hinjewadi Phase 1", "Nagar Road"],
         "Nashik": ["Mumbai Agra Road", "College Road"], "Nagpur": ["Wardha Road", "Amravati Road"],
         "New Delhi": ["Ring Road", "Outer Ring Road", "Mathura Road", "Rajpath"],
         "Bengaluru": ["Silk Board", "Electronic City", "Outer Ring Road", "Hebbal Flyover", "MG Road"],
         "Mysuru": ["Hunsur Road", "Bannur Road"], "Chennai": ["Anna Salai", "OMR", "GST Road", "ECR"],
         "Coimbatore": ["Avinashi Road", "Trichy Road"], "Madurai": ["Bypass Road", "Alagar Kovil Road"],
         "Ahmedabad": ["SG Highway", "CG Road", "Ashram Road"], "Surat": ["Ring Road", "Dumas Road"],
         "Vadodara": ["Old Padra Road", "Race Course Road"], "Jaipur": ["MI Road", "Tonk Road", "JLN Marg"],
         "Jodhpur": ["Paota Road", "Airport Road"], "Noida": ["Noida Expressway", "DND Flyway"],
         "Lucknow": ["Hazratganj", "Shaheed Path"], "Kanpur": ["GT Road", "Mall Road"],
         "Ludhiana": ["Mall Road", "Ferozepur Road"], "Amritsar": ["GT Road", "Majitha Road"],
         "Kolkata": ["Park Street", "EM Bypass", "AJC Bose Road"], "Kochi": ["MG Road", "Seaport Airport Road"],
         "Thiruvananthapuram": ["MG Road", "Kazhakkoottam Bypass"], "Gurugram": ["Cyber City", "Golf Course Road", "NH48"],
         "Faridabad": ["Mathura Road", "Sector 12 Road"], "Hyderabad": ["HITEC City", "Necklace Road", "Banjara Hills Road"]}
MODELS = [("Maruti Suzuki", "Swift", "Car", "Petrol", "LMV", "Hatchback"), ("Maruti Suzuki", "Baleno", "Car", "Petrol", "LMV", "Hatchback"),
          ("Maruti Suzuki", "Dzire", "Car", "Petrol", "LMV", "Sedan"), ("Maruti Suzuki", "Brezza", "Car", "Petrol", "LMV", "Compact SUV"),
          ("Maruti Suzuki", "Ertiga", "Car", "CNG", "LMV", "MUV"), ("Hyundai", "Creta", "Car", "Diesel", "LMV", "SUV"),
          ("Hyundai", "i20", "Car", "Petrol", "LMV", "Hatchback"), ("Hyundai", "Venue", "Car", "Petrol", "LMV", "Compact SUV"),
          ("Hyundai", "Verna", "Car", "Petrol", "LMV", "Sedan"), ("Tata", "Nexon", "Car", "Diesel", "LMV", "Compact SUV"),
          ("Tata", "Punch", "Car", "Petrol", "LMV", "Micro SUV"), ("Tata", "Harrier", "Car", "Diesel", "LMV", "SUV"),
          ("Tata", "LPT 1613", "Truck", "Diesel", "HGV", "Truck"), ("Mahindra", "XUV300", "Car", "Petrol", "LMV", "Compact SUV"),
          ("Mahindra", "Scorpio", "Car", "Diesel", "LMV", "SUV"), ("Mahindra", "Bolero", "Car", "Diesel", "LMV", "SUV"),
          ("Honda", "City", "Car", "Petrol", "LMV", "Sedan"), ("Honda", "Amaze", "Car", "Petrol", "LMV", "Sedan"),
          ("Toyota", "Innova Crysta", "Car", "Diesel", "LMV", "MUV"), ("Toyota", "Fortuner", "Car", "Diesel", "LMV", "SUV"),
          ("Toyota", "Glanza", "Car", "Petrol", "LMV", "Hatchback"), ("Kia", "Seltos", "Car", "Petrol", "LMV", "SUV"),
          ("Kia", "Sonet", "Car", "Petrol", "LMV", "Compact SUV"), ("Skoda", "Rapid", "Car", "Petrol", "LMV", "Sedan"),
          ("Skoda", "Kushaq", "Car", "Petrol", "LMV", "SUV"), ("Volkswagen", "Virtus", "Car", "Petrol", "LMV", "Sedan"),
          ("Renault", "Kwid", "Car", "Petrol", "LMV", "Hatchback"), ("MG", "Hector", "Car", "Petrol", "LMV", "SUV"),
          ("Royal Enfield", "Classic 350", "Bike", "Petrol", "MCWG", "Motorcycle"), ("Royal Enfield", "Bullet 350", "Bike", "Petrol", "MCWG", "Motorcycle"),
          ("Bajaj", "Pulsar 150", "Bike", "Petrol", "MCWG", "Motorcycle"), ("Hero", "Splendor Plus", "Bike", "Petrol", "MCWG", "Motorcycle"),
          ("TVS", "Apache RTR 160", "Bike", "Petrol", "MCWG", "Motorcycle"), ("Honda", "Activa 6G", "Scooter", "Petrol", "MCWG", "Scooter"),
          ("TVS", "Jupiter", "Scooter", "Petrol", "MCWG", "Scooter"), ("Ashok Leyland", "Dost", "Truck", "Diesel", "LGV", "Pickup"),
          ("Eicher", "Pro 2049", "Truck", "Diesel", "LGV", "Truck"), ("Tata", "Ace", "Truck", "Diesel", "LGV", "Mini truck")]
COLORS = ["White", "White", "White", "Silver", "Grey", "Black", "Black", "Red", "Blue", "Brown", "Green", "Yellow"]
FIRST = ["Aarav", "Vivaan", "Aditya", "Arjun", "Rohan", "Karan", "Rahul", "Amit", "Suresh", "Ramesh", "Vikram", "Sanjay",
         "Anil", "Manoj", "Deepak", "Nikhil", "Pranav", "Siddharth", "Varun", "Harsh", "Priya", "Ananya", "Diya", "Isha",
         "Kavya", "Meera", "Neha", "Pooja", "Riya", "Sneha", "Anjali", "Divya", "Lakshmi", "Shalini", "Swati", "Tanvi",
         "Farhan", "Imran", "Salman", "Ayesha", "Zoya", "Joseph", "Thomas", "Mary", "Gurpreet", "Manpreet", "Harjit",
         "Karthik", "Senthil", "Lavanya", "Debashish", "Sourav", "Ankita", "Rakesh", "Mahesh", "Ganesh", "Sunita", "Rekha"]
LAST = ["Sharma", "Verma", "Gupta", "Patel", "Shah", "Mehta", "Desai", "Joshi", "Kulkarni", "Deshmukh", "Patil", "Pawar",
        "Jadhav", "Nair", "Menon", "Pillai", "Iyer", "Raja", "Reddy", "Rao", "Naidu", "Singh", "Kaur", "Gill", "Sandhu",
        "Yadav", "Mishra", "Tiwari", "Pandey", "Roy", "Das", "Ghosh", "Banerjee", "Mukherjee", "Khan", "Ansari",
        "Sheikh", "Varkey", "Thomas", "Fernandes", "D'Souza", "Rathore", "Chauhan", "Bhatia", "Malhotra", "Kapoor"]
INSURERS = [("ICICI Lombard", "ICL"), ("HDFC Ergo", "HDE"), ("Bajaj Allianz", "BAJ"), ("New India Assurance", "NIA"),
            ("United India Ins.", "UII"), ("SBI General", "SBI"), ("Reliance General", "REL"), ("Tata AIG", "TAT"),
            ("Oriental Insurance", "ORI"), ("National Insurance", "NIC"), ("Digit Insurance", "DIG"),
            ("Acko General", "ACK"), ("Royal Sundaram", "RSG"), ("Cholamandalam MS", "CMS"), ("Future Generali", "FGI"),
            ("IFFCO Tokio", "ITG"), ("Kotak General", "KGI"), ("Liberty General", "LGI"), ("Magma HDI", "MHD"),
            ("Shriram General", "SGI")]
CLAIM_TYPES = ["Accident", "Accident", "Accident", "Windshield", "Flood damage", "Fire", "Third-party injury", "Theft"]
VIOLATIONS = [("Speeding", 2000.0), ("Red light jump", 1000.0), ("Riding without helmet", 1000.0), ("Wrong lane", 500.0),
              ("No seat belt", 1000.0), ("Using mobile while driving", 5000.0)]
SERIES = [a + b for a in "ABCDEFGHJKLMNPRSTUVWXYZ" for b in "ABCDEFGHJKLMNPRSTUVWXYZ"]


def load_services():
    """Import each service (it creates and seeds its tables, Phase 3 ones included) and return {device: module}."""
    services = {}
    for device, folder, module in SERVICES:
        sys.path.insert(0, os.path.join(ROOT, folder))
        for name in ("cleansing", "plate_rules", "plate_book", "plate_entry_client", "peers", "context_search", "plate_ocr"):
            sys.modules.pop(name, None)
        spec = importlib.util.spec_from_file_location(module, os.path.join(ROOT, folder, module + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.init_db()
        sys.path.pop(0)
        services[device] = mod
    return services


def backup(services):
    target = os.path.join(ROOT, "backups", datetime.now().strftime("%Y%m%d-%H%M%S"))
    for device, folder, module in SERVICES:
        os.makedirs(os.path.join(target, folder), exist_ok=True)
        for name in os.listdir(os.path.join(ROOT, folder)):
            if name.endswith(".db"):
                shutil.copy2(os.path.join(ROOT, folder, name), os.path.join(target, folder, name))
    return target


def day(d):
    return d.isoformat()


def add_years(d, years):
    """The same day `years` later (28 February for 29 February)."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return d.replace(year=d.year + years, day=28)


def between(rng, start, end):
    return start + timedelta(days=rng.randrange(max(1, (end - start).days)))


def noisy(rng, plate):
    """A misread of a plate: one look-alike in the wrong slot when possible (B as 8, 0 as O ...)."""
    variants = sorted(v for v in rules.format_breaking_variants(plate) if len(v) == len(plate))
    return rng.choice(variants) if variants else plate[:-1]


class Generator:
    def __init__(self, services, counts, seed):
        self.rng = random.Random(seed)
        self.counts = counts
        self.today = date.today()
        self.db = {device: mod.DB_PATH for device, mod in services.items()}
        self.serial = 0

    def next(self):
        self.serial += 1
        return self.serial

    # ---------- the fleet ----------

    def fleet(self, existing):
        self.tag = f"G{len(existing)}"           # in every generated number, so a second run never collides with the first
        rng, districts = self.rng, [(code, st[0], d) for code, st in STATES.items() for d in st[1]]
        weights = [5 if d[1] in ("Pune", "Mumbai", "New Delhi", "Bengaluru", "Chennai") else 2 for _, _, d in districts]
        vehicles, plates = [], set(existing)
        while len(vehicles) < self.counts["registration"]:
            code, state, (district, city, office, areas) = rng.choices(districts, weights)[0]
            plate = f"{code}{district}{rng.choice(SERIES)}{rng.randrange(1, 10000):04d}"
            if plate in plates or not rules.plate_format_valid(plate):
                continue
            plates.add(plate)
            make, model, vtype, fuel, vclass, body = rng.choice(MODELS)
            reg_date = between(rng, date(2008, 1, 1), self.today - timedelta(days=30))
            n = len(vehicles) + 1
            vehicles.append({
                "plate": plate, "state": code, "state_name": state, "city": city, "office": f"{code}{district}",
                "area": rng.choice(areas), "owner": f"{rng.choice(FIRST)} {rng.choice(LAST)}",
                "make": make, "model": model, "type": vtype, "color": rng.choice(COLORS),
                "seen_as": f"{make.split()[0]} {model}",
                "chassis": "MA" + "".join(rng.choices(string.ascii_uppercase + string.digits, k=13)) + f"{n % 100:02d}",
                "engine": f"EN{n:06d}{rng.choice(string.ascii_uppercase)}",
                "reg_date": reg_date, "phone": f"9{rng.randrange(10**8, 10**9)}"})
        self.vehicles = vehicles
        n_theft, n_shred = self.counts["theft_report"], self.counts["shredding_record"]
        if n_theft + n_shred > 0.8 * len(vehicles):
            raise SystemExit("Too many thefts and scrappings for the fleet: raise --set registration=...")
        picked = rng.sample(range(len(vehicles)), n_theft + n_shred)
        self.stolen = [vehicles[i] for i in picked[:n_theft]]
        self.shredded = [vehicles[i] for i in picked[n_theft:]]
        for v in self.shredded:
            v["shred_date"] = between(rng, max(v["reg_date"] + timedelta(days=365), self.today - timedelta(days=1100)),
                                      self.today - timedelta(days=20))
        for v in self.stolen:
            v["theft_date"] = between(rng, max(v["reg_date"] + timedelta(days=60), self.today - timedelta(days=700)),
                                      self.today - timedelta(days=2))

    # ---------- RTO ----------

    def rto(self):
        rng, rows, transfers, pucs = self.rng, [], [], []
        conn = sqlite3.connect(self.db["RTO"])
        conn.executemany("INSERT OR IGNORE INTO rto_office VALUES (?, ?, ?, ?, ?)",
                         [(f"{code}{d}", office, city, st[0], f"{areas[0]}, {city}")
                          for code, st in STATES.items() for d, city, office, areas in st[1]])
        conn.executemany("INSERT OR IGNORE INTO vehicle_model (make, model, fuel_type, vehicle_class, body_type) "
                         "VALUES (?, ?, ?, ?, ?)", [(m, mo, f, c, b) for m, mo, _t, f, c, b in MODELS])
        for v in self.vehicles:
            years = 8 if v["type"] == "Truck" else 15
            upto = add_years(v["reg_date"], years) - timedelta(days=1)
            while upto < self.today and rng.random() < 0.97:              # renewed (most owners do)
                upto = add_years(upto, 5)
            fitness = add_years(v["reg_date"], 2 if v["type"] == "Truck" else 15) - timedelta(days=1)
            if rng.random() < 0.003:                                    # a validity date typed before the registration
                upto = v["reg_date"] - timedelta(days=rng.randrange(30, 900))
            rows.append((v["plate"], v["owner"], f"{v['area']}, {v['city']}", v["chassis"], v["engine"], v["make"],
                         v["model"], day(v["reg_date"]), day(upto), day(fitness)))
        for v in rng.sample(self.vehicles, int(len(self.vehicles) * 0.005)):     # registration entered twice
            plate = noisy(rng, v["plate"]) if rng.random() < 0.4 else v["plate"]
            rows.append((plate, v["owner"], f"{v['area']}, {v['city']}", v["chassis"], v["engine"], v["make"], v["model"],
                         day(v["reg_date"]), day(add_years(v["reg_date"], 15)), ""))
        start = conn.execute("SELECT COALESCE(MAX(reg_id), 0) FROM registration").fetchone()[0]
        conn.executemany("INSERT INTO registration (reg_number, owner_name, owner_address, chassis_number, engine_number, "
                         "make, model, reg_date, reg_valid_upto, fitness_valid_upto) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
        reg_id = {v["plate"]: start + i + 1 for i, v in enumerate(self.vehicles)}
        for v in rng.sample(self.vehicles, min(self.counts["ownership_transfer"], len(self.vehicles))):
            when = between(rng, v["reg_date"], self.today)
            transfers.append((reg_id[v["plate"]], f"{rng.choice(FIRST)} {rng.choice(LAST)}", v["owner"], day(when),
                              rng.choice(["Sale", "Sale", "Sale within family", "Inheritance", "Transfer to company"])))
        for i, v in enumerate(rng.sample(self.vehicles, min(self.counts["puc_certificate"], len(self.vehicles)))):
            tested = between(rng, self.today - timedelta(days=500), self.today)
            fail = rng.random() < 0.05
            pucs.append((reg_id[v["plate"]], f"PUC-{v['office']}-{self.tag}-{i + 1:06d}", day(tested),
                         day(tested + timedelta(days=180 if v["reg_date"].year < 2020 else 365)),
                         round(rng.uniform(2.6, 4.5) if fail else rng.uniform(0.05, 0.6), 2), "Fail" if fail else "Pass"))
        conn.executemany("INSERT INTO ownership_transfer (reg_id, previous_owner, new_owner, transfer_date, reason) "
                         "VALUES (?, ?, ?, ?, ?)", transfers)
        conn.executemany("INSERT INTO puc_certificate (reg_id, certificate_no, tested_on, valid_upto, co_percent, result) "
                         "VALUES (?, ?, ?, ?, ?, ?)", pucs)
        conn.commit()
        conn.close()

    # ---------- Insurance ----------

    def insurance(self):
        rng = self.rng
        conn = sqlite3.connect(self.db["Insurance"])
        conn.executemany("INSERT OR IGNORE INTO insurer (name, irdai_reg_no, head_office, claims_email) VALUES (?, ?, ?, ?)",
                         [(name, f"IRDAI-{200 + i}", "Mumbai", f"claims@{code.lower()}.example")
                          for i, (name, code) in enumerate(INSURERS)])
        shredded = {v["plate"] for v in self.shredded}
        candidates = [v for v in self.vehicles if v["plate"] not in shredded]
        insured = rng.sample(candidates, min(len(candidates), int(self.counts["policy"] * 0.85)))
        policies = []                                   # (vehicle, insurer, start, end, status, type, premium)
        for v in insured:
            name, code = rng.choice(INSURERS)
            roll = rng.random()
            if roll < 0.78:                                               # in force
                start = between(rng, self.today - timedelta(days=360), self.today - timedelta(days=1))
                status = "Active"
            elif roll < 0.93:                                             # lapsed, not renewed
                start = between(rng, max(v["reg_date"], self.today - timedelta(days=1800)), self.today - timedelta(days=400))
                status = "Lapsed"
            else:
                start = between(rng, max(v["reg_date"], self.today - timedelta(days=1500)), self.today - timedelta(days=200))
                status = "Cancelled"
            start = max(start, v["reg_date"])
            end = add_years(start, 1) - timedelta(days=1)
            if status == "Active" and rng.random() < 0.02:                # ended, status never updated
                start, end = start - timedelta(days=400), end - timedelta(days=400)
            policies.append((v, name, code, start, end, status))
        while len(policies) < self.counts["policy"]:                       # renewal history: an older, ended policy
            v = rng.choice(insured)
            name, code = rng.choice(INSURERS)
            start = between(rng, max(v["reg_date"], self.today - timedelta(days=2500)), self.today - timedelta(days=760))
            policies.append((v, name, code, start, start + timedelta(days=364), "Lapsed"))
        for v in self.shredded[: len(self.shredded) // 2]:                  # policy that ended before the scrapping
            name, code = rng.choice(INSURERS)
            start = between(rng, v["reg_date"], max(v["reg_date"] + timedelta(days=1), v["shred_date"] - timedelta(days=400)))
            policies.append((v, name, code, start, start + timedelta(days=364), "Lapsed"))
        rows, holders, payments = [], [], []
        start_id = conn.execute("SELECT COALESCE(MAX(policy_id), 0) FROM policy").fetchone()[0]
        for i, (v, name, code, start, end, status) in enumerate(policies):
            number = f"{code}-{start.year}-{self.tag}-{i + 1:06d}"
            ptype = rng.choice(["Comprehensive", "Comprehensive", "Third-Party"])
            premium = round(rng.uniform(900, 4000) if v["type"] in ("Bike", "Scooter") else rng.uniform(2500, 18000), 0)
            plate = noisy(rng, v["plate"]) if rng.random() < 0.015 else v["plate"]        # plate typed wrong
            rows.append((plate, name, number, ptype, day(start), day(end), premium, status))
            holders.append((start_id + i + 1, v["owner"], v["phone"], f"{v['area']}, {v['city']}", f"KYC-{v['state']}-{self.tag}-{i + 1:06d}"))
            payments.append((start_id + i + 1, day(start), premium, rng.choice(["UPI", "UPI", "Card", "NEFT", "Cash"]), f"RCPT-{self.tag}-{i + 1:06d}"))
        for k in rng.sample(range(len(rows)), int(len(rows) * 0.005)):     # the same policy entered twice
            rows.append(rows[k][:6] + (rows[k][6] + 100, rows[k][7]))
        conn.executemany("INSERT INTO policy (vehicle_reg_no, insurer_name, policy_number, policy_type, start_date, end_date, "
                         "premium_amount, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)
        conn.executemany("INSERT INTO policy_holder (policy_id, holder_name, phone, address, kyc_id) VALUES (?, ?, ?, ?, ?)", holders)
        while len(payments) < self.counts["premium_payment"]:                # instalments
            k = rng.randrange(len(policies))
            payments.append((start_id + k + 1, day(policies[k][3] + timedelta(days=rng.choice([90, 180, 270]))),
                             round(rows[k][6] / 4, 0), "UPI", f"RCPT-{self.tag}-{len(payments) + 1:06d}"))
        conn.executemany("INSERT INTO premium_payment (policy_id, paid_on, amount, mode, receipt_no) VALUES (?, ?, ?, ?, ?)", payments)
        claims, stolen = [], {v["plate"] for v in self.stolen}
        for i in range(self.counts["claim"]):
            k = rng.randrange(len(policies))
            v, name, code, start, end, status = policies[k]
            kind = "Theft" if v["plate"] in stolen and rng.random() < 0.7 else rng.choice(CLAIM_TYPES[:-1])
            when = between(rng, start, min(end, self.today))
            amount = round(rng.uniform(5000, 90000) if kind != "Theft" else rng.uniform(300000, 900000), 0)
            outcome = rng.choice(["Settled", "Settled", "Settled", "Rejected", "Under survey"])
            claims.append((start_id + k + 1, f"CLM-{code}-{self.tag}-{i + 1:06d}", day(when), kind, amount,
                           round(amount * rng.uniform(0.6, 0.95), 0) if outcome == "Settled" else (0.0 if outcome == "Rejected" else None),
                           outcome, f"{rng.choice(FIRST)[0]}. {rng.choice(LAST)}"))
        conn.executemany("INSERT INTO claim (policy_id, claim_no, claim_date, claim_type, amount_claimed, amount_settled, "
                         "status, surveyor) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", claims)
        conn.commit()
        conn.close()

    # ---------- Theft / Shredding ----------

    def theft(self):
        rng = self.rng
        conn = sqlite3.connect(self.db["Theft/Shredding"])
        cities = sorted({(st[0], d[1]) for st in STATES.values() for d in st[1]})
        stations = [(f"{area} PS, {city}", city, state, f"0{rng.randrange(10**9, 10**10)}", f"Insp. {rng.choice(FIRST)[0]}. {rng.choice(LAST)}")
                    for code, st in STATES.items() for _d, city, _o, areas in st[1] for area in areas for state in [st[0]]]
        conn.executemany("INSERT OR IGNORE INTO police_station (name, city, state, phone, officer_in_charge) VALUES (?, ?, ?, ?, ?)", stations)
        centers = [(f"{name} Recyclers, {city}", city, state, f"RVSF-{rng.randrange(1000, 9999)}")
                   for state, city in cities for name in ("Green", "Metro")]
        conn.executemany("INSERT OR IGNORE INTO scrap_center (name, city, state, authorization_no) VALUES (?, ?, ?, ?)", centers)
        by_city = {}
        for name, city, *_ in stations:
            by_city.setdefault(city, []).append(name)
        centers_by_city = {}
        for name, city, *_ in centers:
            centers_by_city.setdefault(city, []).append(name)
        thefts, recover = [], []
        for i, v in enumerate(self.stolen):
            recovered = rng.random() < 0.4
            thefts.append((v["plate"], day(v["theft_date"]), rng.choice(by_city[v["city"]]),
                           f"FIR-{v['theft_date'].year}-{self.tag}-{i + 1:05d}", "Recovered" if recovered else "Reported"))
            recover.append(recovered)
        for k in rng.sample(range(len(thefts)), int(len(thefts) * 0.01)):    # the same FIR entered twice
            thefts.append(thefts[k])
            recover.append(False)
        start = conn.execute("SELECT COALESCE(MAX(theft_id), 0) FROM theft_report").fetchone()[0]
        conn.executemany("INSERT INTO theft_report (vehicle_no, report_date, police_station, fir_number, status) VALUES (?, ?, ?, ?, ?)", thefts)
        recoveries = []
        for i, (row, rec) in enumerate(zip(thefts, recover)):
            if rec:
                when = date.fromisoformat(row[1]) + timedelta(days=rng.randrange(3, 120))
                recoveries.append((start + i + 1, day(min(when, self.today)), f"{rng.choice(ROADS.get(row[2].split(', ')[-1], ['Highway']))}",
                                   rng.choice(["No damage", "Minor damage", "Stripped of parts", "Plates changed"]), row[2]))
        conn.executemany("INSERT INTO theft_recovery (theft_id, recovered_on, recovered_at, vehicle_condition, recovered_by) "
                         "VALUES (?, ?, ?, ?, ?)", recoveries)
        shreds = [(v["plate"], day(v["shred_date"]), rng.choice(centers_by_city[v["city"]]), f"SCR-{v['shred_date'].year}-{self.tag}-{i + 1:05d}")
                  for i, v in enumerate(self.shredded)]
        start = conn.execute("SELECT COALESCE(MAX(shred_id), 0) FROM shredding_record").fetchone()[0]
        conn.executemany("INSERT INTO shredding_record (vehicle_no, shred_date, scrap_center, certificate_no) VALUES (?, ?, ?, ?)", shreds)
        conn.executemany("INSERT INTO shredding_handover (shred_id, handed_over_on, handed_over_by, documents_checked, odometer_km) "
                         "VALUES (?, ?, ?, ?, ?)",
                         [(start + i + 1, day(v["shred_date"] - timedelta(days=rng.randrange(1, 10))), v["owner"],
                           "RC, insurance copy, owner ID, chassis imprint", rng.randrange(20000, 250000))
                          for i, v in enumerate(self.shredded)])
        conn.commit()
        conn.close()

    # ---------- ANPR ----------

    def anpr(self):
        rng = self.rng
        conn = sqlite3.connect(self.db["ANPR"])
        locations = []
        for code, st in STATES.items():
            for city in dict.fromkeys(d[1] for d in st[1]):
                for road in ROADS.get(city, ["Main Road"]):
                    locations.append((f"{road}, {city}", road, city, st[0], round(rng.uniform(8, 32), 4),
                                      round(rng.uniform(70, 92), 4), rng.choice(["City road", "Arterial road", "Highway", "Junction"])))
        conn.executemany("INSERT OR IGNORE INTO location (name, area, city, state, latitude, longitude, road_type) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", locations)
        loc_ids = {name: (lid, city) for lid, name, city in conn.execute("SELECT location_id, name, city FROM location")}
        cameras, n = [], 1000
        for name, (lid, city) in loc_ids.items():
            for _ in range(2):
                cameras.append((f"CAM-{n}", lid, rng.choice(["Hikvision iDS-TCM403", "Axis Q1700-LE", "Bosch DINION 7100",
                                                              "CP Plus ANPR-500"]), day(between(rng, date(2020, 1, 1), date(2025, 12, 31))),
                                "Active" if rng.random() > 0.05 else "Under repair"))
                n += 1
        conn.executemany("INSERT OR IGNORE INTO camera VALUES (?, ?, ?, ?, ?)", cameras)
        cams_by_city = {}
        for cam, lid, *_ in cameras:
            name = next(nm for nm, (i, _c) in loc_ids.items() if i == lid)
            cams_by_city.setdefault(loc_ids[name][1], []).append((cam, name))
        all_cams = [c for cs in cams_by_city.values() for c in cs]
        shredded = {v["plate"] for v in self.shredded}
        on_road = [v for v in self.vehicles if v["plate"] not in shredded]
        unregistered = []
        while len(unregistered) < max(50, self.counts["vehicle_capture"] // 40):
            code = rng.choice(list(STATES))
            d, city, _o, _a = rng.choice(STATES[code][1])
            make, model, vtype, *_ = rng.choice(MODELS)
            unregistered.append({"plate": f"{code}{d}{rng.choice(SERIES)}{rng.randrange(1, 10000):04d}", "city": city,
                                 "type": vtype, "color": rng.choice(COLORS), "seen_as": f"{make.split()[0]} {model}"})
        rows = []
        for _ in range(self.counts["vehicle_capture"]):
            roll = rng.random()
            v = (rng.choice(self.shredded) if roll < 0.01 and self.shredded else       # a scrapped car's plates on the road
                 rng.choice(self.stolen) if roll < 0.04 and self.stolen else
                 rng.choice(unregistered) if roll < 0.07 else rng.choice(on_road))
            cam, loc = rng.choice(cams_by_city[v["city"]] if rng.random() < 0.85 and v["city"] in cams_by_city else all_cams)
            seen = datetime.combine(between(rng, self.today - timedelta(days=120), self.today), datetime.min.time()) + \
                timedelta(seconds=rng.randrange(6 * 3600, 23 * 3600))
            if seen > datetime.now():
                seen -= timedelta(days=1)
            plate, model = v["plate"], v["seen_as"]
            noise = rng.random()
            if noise < 0.03:
                plate = noisy(rng, plate)                                     # camera misread
            elif noise < 0.034:
                model = rng.choice(["xx", "sgd", "unknown car"])               # junk typed by the operator
            elif noise < 0.037:
                model = rng.choice([m for m in MODELS if m[2] == v["type"]] or MODELS)
                model = f"{model[0].split()[0]} {model[1]}"                    # does not match the registration
            rows.append((plate, seen.strftime("%Y-%m-%d %H:%M:%S"), loc, v["color"], v["type"], model, cam))
        for k in rng.sample(range(len(rows)), int(len(rows) * 0.01)):         # the same capture saved twice
            rows.append(rows[k])
        start = conn.execute("SELECT COALESCE(MAX(capture_id), 0) FROM vehicle_capture").fetchone()[0]
        conn.executemany("INSERT INTO vehicle_capture (plate_number, capture_ts, location, vehicle_color, vehicle_type, "
                         "make_model_seen, camera_id) VALUES (?, ?, ?, ?, ?, ?, ?)", rows)
        violations = []
        for k in rng.sample(range(len(rows)), min(self.counts["capture_violation"], len(rows))):
            kind, fine = rng.choice(VIOLATIONS)
            limit = rng.choice([40, 50, 60, 80, 100]) if kind == "Speeding" else None
            violations.append((start + k + 1, kind, limit + rng.randrange(8, 45) if limit else None, limit, fine))
        conn.executemany("INSERT INTO capture_violation (capture_id, violation_type, recorded_speed, speed_limit, fine_amount) "
                         "VALUES (?, ?, ?, ?, ?)", violations)
        issues = ["Lens cleaned", "IR illuminator replaced", "Firmware update", "Realigned after storm", "Power supply replaced",
                  "Night-time misreads of B as 8 reported", "Network switch replaced", "Housing resealed against rain"]
        conn.executemany("INSERT INTO camera_maintenance (camera_id, serviced_on, issue, technician) VALUES (?, ?, ?, ?)",
                         [(rng.choice(cameras)[0], day(between(rng, self.today - timedelta(days=700), self.today)),
                           rng.choice(issues), f"{rng.choice(FIRST)[0]}. {rng.choice(LAST)}")
                          for _ in range(self.counts["camera_maintenance"])])
        conn.commit()
        conn.close()

    # ---------- MoT ----------

    def mot(self):
        rng = self.rng
        conn = sqlite3.connect(self.db["MoT"])
        conn.executemany("INSERT OR IGNORE INTO zone (state_code, name, headquarters) VALUES (?, ?, ?)",
                         [(code, f"{st[0]} Zone", st[1][0][1]) for code, st in STATES.items()])
        zones = [z for (z,) in conn.execute("SELECT zone_id FROM zone")]
        conn.executemany("INSERT INTO officer (zone_id, name, rank, phone) VALUES (?, ?, ?, ?)",
                         [(z, f"{rng.choice(FIRST)[0]}. {rng.choice(LAST)}", rng.choice(["Motor Vehicle Inspector", "Enforcement Officer",
                                                                                        "Assistant Inspector"]),
                           f"9{rng.randrange(10**8, 10**9)}") for z in zones for _ in range(3)])
        conn.commit()
        conn.close()


def reset_cleansing(services):
    """The bulk rows arrived outside the services: every device cleans all its records again at its next start."""
    for device, mod in services.items():
        if not hasattr(mod, "cleanser"):
            continue
        conn = sqlite3.connect(mod.DB_PATH)
        try:
            conn.execute("DELETE FROM cleansing_state WHERE key IN ('built', 'epoch')")
            conn.execute("DELETE FROM cleansing_changes")
            conn.commit()
        except sqlite3.OperationalError:
            pass                                                            # never cleaned yet: nothing to reset
        finally:
            conn.close()


def licences(services, seed):
    """A driving licence (date of birth) for the owner of every registration that has none. The same vehicle entered
    twice (same chassis) gets the same owner; company owners hold no licence. Ages today follow a plausible spread,
    and every owner was at least 18 on the registration date."""
    conn = sqlite3.connect(services["RTO"].DB_PATH)
    conn.executescript(services["RTO"].rto_context.SCHEMA)
    today = date.today()
    rows = conn.execute("SELECT reg_id, reg_number, owner_name, chassis_number, reg_date FROM registration WHERE reg_id NOT IN "
                        "(SELECT reg_id FROM driving_licence) ORDER BY reg_id").fetchall()
    out = []
    for rid, plate, owner, chassis, reg_date in rows:
        if not owner or any(w in owner for w in ("Co.", "Ltd", "Transport", "Logistics", "Pvt")):
            continue
        rng = random.Random(f"{seed}-{chassis or plate}-{owner}")
        try:
            registered = date.fromisoformat(str(reg_date)[:10])
        except ValueError:
            registered = today
        lo, hi = rng.choices([(18, 25), (26, 35), (36, 45), (46, 60), (61, 80)], [12, 28, 25, 23, 12])[0]
        age = rng.randint(lo, hi)
        youngest = 19 + (today - registered).days // 365          # at least 18 on the registration date
        if age < youngest:
            age = youngest + rng.randint(0, 6)
        born = add_years(today, -age) - timedelta(days=rng.randrange(1, 365))
        issued = min(registered, add_years(born, 18 + rng.randint(0, 4)))
        code = re.match(r"[A-Z]{2}\d{1,2}", plate or "")
        out.append((rid, f"{code.group(0) if code else 'XX00'} {issued.year}{rid:07d}", owner, day(born), day(issued),
                    day(add_years(issued, 20))))
    conn.executemany("INSERT OR IGNORE INTO driving_licence (reg_id, licence_no, holder_name, date_of_birth, issued_on, "
                     "valid_upto) VALUES (?, ?, ?, ?, ?, ?)", out)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM driving_licence").fetchone()[0]
    conn.close()
    print(f"  driving licences: {len(out)} added, {total} in all")


def clean_now(services):
    """Each device's cleansing, run here until nothing changes; MoT receives ANPR's reports through its own route."""
    import time
    mot = services["MoT"].app.test_client()
    for mod in services.values():
        if hasattr(mod, "cleanser"):
            mod.cleanser.fetch = lambda d, since=0, epoch=None: services[d].cleanser.evidence(since, epoch)
            mod.cleanser.post = lambda url, payload: mot.post("/reports", json=payload).status_code == 201
            mod.cleanser.notify_peers = lambda round_: None
            mod.cleanser.peers = dict(mod.cleanser.peers, MoT=mod.cleanser.peers.get("MoT") or "http://mot")
    for round_ in range(1, 5):
        changed = False
        for device, mod in services.items():
            if hasattr(mod, "cleanser"):
                start = time.time()
                out = mod.cleanser.run()
                changed = changed or out["status"] == "cleaned" and out.get("changed", True)
                print(f"  cleaning round {round_}: {device:16s} {out['status']:9s} {time.time() - start:5.1f}s")
        if not changed:
            break


def dictionaries(services):
    for device, mod in services.items():
        if hasattr(mod, "desk"):
            conn = sqlite3.connect(mod.desk.book.path)
            plates = conn.execute("SELECT COUNT(*) FROM plates").fetchone()[0]
            learned = conn.execute("SELECT COUNT(*) FROM variants WHERE origin <> 'generated'").fetchone()[0]
            conn.close()
            print(f"  plate dictionary {device:16s} {plates} plates, {learned} misreads learned by cleansing")


def report(services):
    for device, mod in services.items():
        conn = sqlite3.connect(mod.DB_PATH)
        tables = [t for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                             "AND name NOT LIKE 'cleansing_%' AND name NOT LIKE '%_clean' ORDER BY name")]
        print(f"{device:16s}", ", ".join(f"{t} {conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}" for t in tables))
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scale", type=float, default=1.0, help="multiply every default size")
    parser.add_argument("--set", action="append", default=[], metavar="TABLE=N", help="size of one table, e.g. theft_report=10000")
    parser.add_argument("--seed", type=int, default=2026, help="random seed (same seed, same data)")
    parser.add_argument("--clean-only", action="store_true", help="add nothing; clean the records already there")
    parser.add_argument("--no-clean", action="store_true", help="insert only; leave the cleaning to the services")
    parser.add_argument("--licences-only", action="store_true",
                        help="add nothing else; give registrations without one their owner's driving licence")
    args = parser.parse_args()
    counts = {k: int(v * args.scale) for k, v in COUNTS.items()}
    for item in args.set:
        name, _, value = item.partition("=")
        if name not in counts:
            raise SystemExit(f"unknown table {name!r}; one of: {', '.join(counts)}")
        counts[name] = int(value)

    services = load_services()
    print("backup:", backup(services))
    if args.licences_only:
        licences(services, args.seed)
        return
    if not args.clean_only:
        gen = Generator(services, counts, args.seed)
        conn = sqlite3.connect(services["RTO"].DB_PATH)
        existing = {p for (p,) in conn.execute("SELECT reg_number FROM registration")}
        conn.close()
        gen.fleet(existing)
        for step in (gen.rto, gen.insurance, gen.theft, gen.anpr, gen.mot):
            step()
        licences(services, args.seed)
        reset_cleansing(services)
    if args.no_clean:
        report(services)
        print("Done. Start the services: each one cleans all its records once, and ANPR then reports to MoT.")
        return
    print("cleaning (every device, reading the others' data directly):")
    clean_now(services)
    report(services)
    dictionaries(services)
    print("Done: data cleaned, plate dictionaries filled, MoT reports delivered.")


if __name__ == "__main__":
    main()
