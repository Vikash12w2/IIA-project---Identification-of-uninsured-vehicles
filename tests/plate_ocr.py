"""OCR for a photo of a number plate: OpenCV clean-up + Tesseract.

Used by the ANPR add form and the Vehicle 360 search (shared like plate_rules.py: when the devices run on
separate machines, copy this file next to the ANPR service and the client).

The image should show the plate itself (cropped), straight on. Only the plate text is read; colour, type and
make/model are typed by the operator (OCR cannot tell them). The text read here is NOT trusted: at ANPR it goes
through the same plate check as a typed plate, and in Vehicle 360 it is searched approximately like a typed plate.
"""
import base64
import os
import re
import shutil

try:
    import cv2
    import numpy as np
    import pytesseract
except ImportError:              # OCR is optional: typing the plate always works
    cv2 = np = pytesseract = None

WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESS_CONFIG = "-c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
PLATE_FORMAT = re.compile(r"^[A-Z]{2}\d{1,2}[A-Z]{2}\d{4}$")


def _tesseract_cmd():
    return shutil.which("tesseract") or (WINDOWS_TESSERACT if os.path.exists(WINDOWS_TESSERACT) else None)


def available():
    """True when OpenCV, pytesseract and the Tesseract program are all installed."""
    return pytesseract is not None and _tesseract_cmd() is not None


def decode(data):
    """The uploaded bytes as an OpenCV image, or None if they are not an image."""
    if cv2 is None or not data:
        return None
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def preview_data_uri(data, max_width=480):
    """The uploaded image as a small PNG data: URI, to show it back without storing it (Vehicle 360 search)."""
    image = decode(data)
    if image is None:
        return ""
    if image.shape[1] > max_width:
        image = cv2.resize(image, None, fx=max_width / image.shape[1], fy=max_width / image.shape[1],
                           interpolation=cv2.INTER_AREA)
    ok, png = cv2.imencode(".png", image)
    return "data:image/png;base64," + base64.b64encode(png.tobytes()).decode("ascii") if ok else ""


def _variants(image):
    """Black-on-white versions of the plate for Tesseract: enlarged, smoothed, Otsu threshold (and inverted)."""
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    scale = max(1.0, 160.0 / gray.shape[0])
    gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    gray = cv2.bilateralFilter(gray, 9, 75, 75)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if np.mean(binary) < 127:                    # light text on a dark plate: make the text black
        binary = cv2.bitwise_not(binary)
    pad = cv2.copyMakeBorder(binary, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
    return [pad, gray]


def read_plate(data):
    """{"text": normalised plate text, "confidence": 0-1, "error": message or ""} for an uploaded image."""
    if not available():
        return {"text": "", "confidence": 0.0, "error": "OCR is not installed on this machine (Tesseract)."}
    image = decode(data)
    if image is None:
        return {"text": "", "confidence": 0.0, "error": "The file is not an image."}
    pytesseract.pytesseract.tesseract_cmd = _tesseract_cmd()
    best = None
    for variant in _variants(image):
        for psm in (7, 8):                       # one line of text / one word
            try:
                text = pytesseract.image_to_string(variant, config=f"--psm {psm} {TESS_CONFIG}")
            except pytesseract.TesseractError:
                continue
            text = re.sub(r"[^A-Z0-9]", "", text.upper())
            if text.startswith("IND") and len(text) > 10:           # the "IND" strip of a high-security plate
                text = text[3:]
            if text and (best is None or (PLATE_FORMAT.match(text) and not PLATE_FORMAT.match(best[0]))):
                best = (text, variant, psm)
    if best is None:
        return {"text": "", "confidence": 0.0, "error": "No plate text could be read from the image."}
    return {"text": best[0], "confidence": _confidence(*best[1:]), "error": ""}


def _confidence(variant, psm):
    """Mean word confidence (0-1). Tesseract reports 0 for every word when a character whitelist is set,
    so the confidence comes from a second read of the same image without it."""
    try:
        found = pytesseract.image_to_data(variant, config=f"--psm {psm}", output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractError:
        return 0.0
    confs = [float(c) for w, c in zip(found["text"], found["conf"]) if str(w).strip() and float(c) >= 0]
    return round(sum(confs) / len(confs) / 100.0, 2) if confs else 0.0
