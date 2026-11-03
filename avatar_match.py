"""Small, in-memory visual fingerprints; no facial recognition or external service."""
from dataclasses import dataclass
import hashlib
import io

from PIL import Image, ImageOps


@dataclass(frozen=True)
class View:
    digest: str
    dhash: int
    pixels: bytes


@dataclass(frozen=True)
class Fingerprint:
    normal: View
    mirrored: View


def view(image):
    small = image.resize((32, 32), Image.Resampling.LANCZOS)
    grey = image.convert("L").resize((17, 16), Image.Resampling.LANCZOS).tobytes()
    bits = 0
    for y in range(16):
        for x in range(16):
            bits = (bits << 1) | (grey[y * 17 + x] > grey[y * 17 + x + 1])
    pixels = small.tobytes()
    return View(hashlib.sha256(pixels).hexdigest(), bits, pixels)


def fingerprint(data):
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Avatar exceeds 2 MiB")
    with Image.open(io.BytesIO(data)) as source:
        if source.width * source.height > 1024 * 1024:
            raise ValueError("Avatar dimensions too large")
        # Discord assets are requested as static PNG; use the first frame if animated.
        source.seek(0)
        rgba = ImageOps.exif_transpose(source).convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        image = background.convert("RGB")
        return Fingerprint(view(image), view(ImageOps.mirror(image)))


def compare(candidate, staff, max_distance=20, max_error=0.12):
    """Return transformation and measurements, not a probability of impersonation."""
    best = None
    for label, other in (("normal", candidate.normal), ("mirrored", candidate.mirrored)):
        distance = (other.dhash ^ staff.normal.dhash).bit_count()
        error = sum(abs(a - b) for a, b in zip(other.pixels, staff.normal.pixels)) / (len(other.pixels) * 255)
        exact = other.digest == staff.normal.digest
        if exact or (distance <= max_distance and error <= max_error):
            result = {"kind": ("same normalized image" if exact else "similar image") + (" (mirrored)" if label == "mirrored" else ""), "distance": distance, "error": error}
            if best is None or (distance, error) < (best["distance"], best["error"]):
                best = result
    return best
