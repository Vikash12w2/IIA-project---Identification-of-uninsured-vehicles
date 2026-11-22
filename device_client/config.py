# ANPR_SERVICE      = "http://127.0.0.1:5001"
# INSURANCE_SERVICE = "http://127.0.0.1:5002"
# RTO_SERVICE       = "http://127.0.0.1:5003"
# THEFT_SERVICE     = "http://127.0.0.1:5004"
# MOT_SERVICE       = "http://127.0.0.1:5005"
ANPR_SERVICE      = "http://192.168.45.115:5001"
INSURANCE_SERVICE = "http://192.168.52.125:5002"
RTO_SERVICE       = "http://192.168.45.115:5003"
THEFT_SERVICE     = "http://192.168.45.119:5004"
MOT_SERVICE       = "http://192.168.52.125:5005"

REQUEST_TIMEOUT_SECONDS = 15

# Approximate plate candidate matching. Similarity is 1 - (edit distance / max length).
PLATE_MATCH_THRESHOLD = 0.85   # discovery cut-off on the weighted plate score; ranking by confidence does the rest
PLATE_MATCH_MAX_RESULTS = 100
PLATE_MATCH_MAX_SLIPS = 2      # Vehicle 360 search also lists plates up to this many characters different

# Matching tuning (see integration_engine.py).
# "weighted" = Levenshtein with cheap OCR look-alike substitutions; "levenshtein" = plain baseline.
PLATE_MATCH_ALGORITHM = "weighted"
PLATE_CONFUSION_COST = 0.3        # substitution cost for look-alike pairs (0/O, 1/I, 8/B, 5/S ...)
PLATE_CONTEXT_WEIGHT = 0.3        # share of confidence that comes from colour/type/model agreement
