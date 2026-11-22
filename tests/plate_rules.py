"""Plate rules shared by every device: the format, look-alike repair, similarity and colour/type/model comparison.

Pure functions only (no database, no network). The source services use them for the check at entry and their own
plate cleansing page; the Vehicle 360 client uses the same functions for its cross-database search.
When the devices run on separate machines, copy this file next to each service (like plate_entry_client.py).
"""
import re
from difflib import SequenceMatcher

# Tuning (the Vehicle 360 client passes its own values from device_client/config.py)
CONFUSION_COST = 0.3        # substitution cost for look-alike pairs (0/O, 1/I, 8/B, 5/S ...)
CONTEXT_WEIGHT = 0.3        # share of confidence that comes from colour/type/model agreement
MATCH_THRESHOLD = 0.85      # minimum weighted plate score for a "closest plate"
RETRY_LIMIT = 2             # "No" answers allowed per entry
CLOSE_PLATE_THRESHOLD = 0.90  # RTO warning: a plate this similar is shown as "close"


def normalize_plate(value):
    """Normalize plate formatting for comparison without changing source data."""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


# Supported registration format: state (2 letters) + district (1-2 digits) + series (2 letters) + number (EXACTLY 4 digits)
# MH12AB1234 is valid; MH12AB123 (dropped digit) is not.
PLATE_FORMAT = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{2}\d{4}$")


def plate_format_valid(value):
    return bool(PLATE_FORMAT.match(normalize_plate(value)))


# ------------------------------------------------------------
# Similarity
#
# Scanned plates fail in predictable ways: OCR confuses look-alike
# characters (0/O/D/Q, 1/I/L, 8/B, 5/S, 2/Z, 6/G, 4/A). Plain Levenshtein
# charges every substitution the same, so "DL8CAFI234" (I read as 1) scores
# the same as "DL8CAF1284" (a different digit). The weighted distance
# charges a look-alike substitution only CONFUSION_COST, so OCR-style
# noise ranks above an unrelated one-character difference.
# ------------------------------------------------------------

CONFUSION_GROUPS = ("0OQD", "1IL", "2Z", "5S", "6G", "8B", "4A")
CONFUSABLE_PAIRS = {frozenset((x, y)) for group in CONFUSION_GROUPS for x in group for y in group if x != y}
CONFUSABLE_PARTNERS = {}
for _group in CONFUSION_GROUPS:
    for _ch in _group:
        CONFUSABLE_PARTNERS.setdefault(_ch, set()).update(set(_group) - {_ch})

# The first two characters of each look-alike group are the usual misread (0/O, 1/I, 2/Z, 5/S, 6/G, 8/B, 4/A);
# the rest of a group (Q, D, L) is a rarer one.
COMMON_LOOKALIKES = {frozenset(group[:2]) for group in CONFUSION_GROUPS}


def levenshtein_distance(left, right):
    """Return the standard Levenshtein edit distance using O(min(n, m)) space."""
    if len(left) < len(right):
        left, right = right, left
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for i, char_left in enumerate(left, 1):
        current = [i]
        for j, char_right in enumerate(right, 1):
            current.append(min(
                current[j - 1] + 1,
                previous[j] + 1,
                previous[j - 1] + (char_left != char_right),
            ))
        previous = current
    return previous[-1]


def plate_similarity(left, right):
    """Normalized Levenshtein similarity in [0, 1] (the plain baseline)."""
    a, b = normalize_plate(left), normalize_plate(right)
    if not a or not b:
        return 0.0
    return 1.0 - levenshtein_distance(a, b) / max(len(a), len(b))


def one_edit_apart(left, right):
    """True when two different plates differ by ONE slip: a character changed, added or dropped, or two
    neighbouring characters swapped (MH12AB1234 / MH12AB1284 / MH12AB1243)."""
    a, b = normalize_plate(left), normalize_plate(right)
    if a == b:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    if abs(len(a) - len(b)) == 1:
        short, long_ = sorted((a, b), key=len)
        return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))
    return False


def weighted_plate_distance(left, right, confusion_cost=CONFUSION_COST):
    """Levenshtein distance where OCR look-alike substitutions are cheap."""
    a, b = normalize_plate(left), normalize_plate(right)
    if not a:
        return float(len(b))
    if not b:
        return float(len(a))
    previous = [float(j) for j in range(len(b) + 1)]
    for i, ca in enumerate(a, 1):
        current = [float(i)]
        for j, cb in enumerate(b, 1):
            if ca == cb:
                sub = 0.0
            elif frozenset((ca, cb)) in CONFUSABLE_PAIRS:
                sub = confusion_cost
            else:
                sub = 1.0
            current.append(min(current[j - 1] + 1, previous[j] + 1, previous[j - 1] + sub))
        previous = current
    return previous[-1]


def weighted_plate_similarity(left, right, confusion_cost=CONFUSION_COST):
    a, b = normalize_plate(left), normalize_plate(right)
    if not a or not b:
        return 0.0
    return max(0.0, 1.0 - weighted_plate_distance(a, b, confusion_cost) / max(len(a), len(b)))


# ------------------------------------------------------------
# Noisy variants (only used to seed a database's dictionary the first time it is built)
# ------------------------------------------------------------

def plate_variants(plate):
    """Likely noisy versions of ONE plate (an illustrative set, never every possible corruption).

    - 1 or 2 look-alike substitutions (0/O/Q/D, 1/I/L, 2/Z, 5/S, 6/G, 8/B, 4/A)
    - one character dropped
    - one character doubled
    - two neighbouring characters swapped
    """
    plate = normalize_plate(plate)
    out = set()
    slots = [(i, sorted(CONFUSABLE_PARTNERS[ch])) for i, ch in enumerate(plate) if ch in CONFUSABLE_PARTNERS]
    for i, partners in slots:                                   # one substitution
        for x in partners:
            out.add(plate[:i] + x + plate[i + 1:])
    for n, (i, pi) in enumerate(slots):                         # two substitutions
        for j, pj in slots[n + 1:]:
            for x in pi:
                for y in pj:
                    out.add(plate[:i] + x + plate[i + 1:j] + y + plate[j + 1:])
    for i in range(len(plate)):
        out.add(plate[:i] + plate[i + 1:])                      # dropped character
        out.add(plate[:i] + plate[i] + plate[i:])               # doubled character
    for i in range(len(plate) - 1):
        if plate[i] != plate[i + 1]:
            out.add(plate[:i] + plate[i + 1] + plate[i] + plate[i + 2:])   # swapped neighbours
    out.discard(plate)
    return {v for v in out if len(v) >= 5}


def format_breaking_variants(plate):
    """The variants worth storing: those that are NOT themselves a valid plate (a valid one is another vehicle)."""
    return {v for v in plate_variants(plate) if not plate_format_valid(v)}


# ------------------------------------------------------------
# Positional repair: look-alikes sitting in the wrong slot (MHI2AB1234 -> MH12AB1234)
# ------------------------------------------------------------

def positional_repair(plate, max_changes=3):
    """Valid plates reachable by swapping characters for look-alikes that sit in the wrong slot.

    MHI2AB1234: the district slot wants a digit and holds I, so I -> 1 gives MH12AB1234.
    Only the fewest changes are returned; one result = a clear repair, several = ambiguous.
    """
    norm = normalize_plate(plate)
    if not norm or plate_format_valid(norm):
        return []
    options = [[ch] + sorted(CONFUSABLE_PARTNERS.get(ch, ())) for ch in norm]
    best = {}

    def walk(i, built, changes):
        if i == len(norm):
            if plate_format_valid(built):
                best[built] = changes
            return
        for k, ch in enumerate(options[i]):
            used = changes + (1 if k else 0)
            if used <= max_changes:
                walk(i + 1, built + ch, used)

    walk(0, "", 0)
    if not best:
        return []
    fewest = min(best.values())
    return sorted(p for p, n in best.items() if n == fewest)


def repair_likelihood_cost(plate, repaired):
    """How unlikely a repair is: 1 per common look-alike swap, 2 per rare one (lower = more likely)."""
    a, b = normalize_plate(plate), normalize_plate(repaired)
    return sum(1 if frozenset((x, y)) in COMMON_LOOKALIKES else 2 for x, y in zip(a, b) if x != y)


def ranked_repairs(plate, present=()):
    """positional_repair results, most likely first: plates already in the database, then common look-alikes."""
    return sorted(positional_repair(plate),
                  key=lambda p: (p not in present, repair_likelihood_cost(plate, p), p))


# ------------------------------------------------------------
# Context (colour, vehicle type, make/model)
#
# A candidate plate's profile is built from the records that hold it. Observed context
# (from the scanned image / the record being added) is compared to it.
# ------------------------------------------------------------

COLOR_ALIASES = {"gray": "grey", "silvery": "silver"}
CONTEXT_KEYS = ("color", "vehicle_type", "make_model")


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def normalize_context(context):
    """Keep only non-empty color / vehicle_type / make_model values."""
    ctx = {}
    for key in CONTEXT_KEYS:
        value = clean_text((context or {}).get(key))
        if value:
            ctx[key] = COLOR_ALIASES.get(value, value) if key == "color" else value
    return ctx


def text_match(observed, known):
    """Fuzzy make/model comparison tolerant of extra words ('Maruti Swift' ~ 'Maruti Suzuki Swift')."""
    obs_tokens = [t for t in re.split(r"[^a-z0-9]+", observed) if t]
    known_tokens = [t for t in re.split(r"[^a-z0-9]+", known) if t]
    if not obs_tokens or not known_tokens:
        return False
    def has(token, pool):
        return any(token == p or SequenceMatcher(None, token, p).ratio() >= 0.85 for p in pool)
    shorter, longer = sorted((obs_tokens, known_tokens), key=len)
    return all(has(t, longer) for t in shorter)


def vehicle_profile(contexts):
    """Profile of one vehicle from the context dicts ({color, vehicle_type, make_model}) of its records."""
    profile = {key: set() for key in CONTEXT_KEYS}
    for ctx in contexts:
        for key, value in normalize_context(ctx).items():
            profile[key].add(value)
    return profile


def context_comparison(context, profile):
    """Compare observed context to a candidate profile.

    An attribute is a match, a conflict (candidate has values, none agree),
    or unknown (candidate has no value) — unknown never counts against it.
    """
    matches, conflicts, unknown = [], [], []
    for key, observed in normalize_context(context).items():
        known = profile.get(key) or set()
        if not known:
            unknown.append(key)
        elif key == "make_model":
            (matches if any(text_match(observed, k) for k in known) else conflicts).append(key)
        else:
            (matches if observed in known else conflicts).append(key)
    decided = len(matches) + len(conflicts)
    return {"matches": matches, "conflicts": conflicts, "unknown": unknown,
            "score": (len(matches) / decided) if decided else None}


def combined_confidence(plate_sim, ctx_score, weight=CONTEXT_WEIGHT):
    """Blend plate similarity with context agreement. No evidence -> plate score only."""
    if ctx_score is None:
        return plate_sim
    return (1 - weight) * plate_sim + weight * ctx_score
