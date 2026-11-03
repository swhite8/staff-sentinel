import io
import unittest

from PIL import Image, ImageDraw, ImageEnhance, ImageOps

from avatar_match import fingerprint, compare


def encoded(image, format="PNG", **kwargs):
    output = io.BytesIO()
    image.save(output, format=format, **kwargs)
    return output.getvalue()


def sample():
    image = Image.new("RGB", (128, 128), "#225588")
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 12, 75, 100), fill="#e9b45a")
    draw.rectangle((80, 40, 120, 65), fill="#781a44")
    draw.polygon([(60, 90), (115, 115), (85, 72)], fill="#f3edd1")
    return image


class AvatarTests(unittest.TestCase):
    def test_identical(self):
        value = fingerprint(encoded(sample()))
        self.assertEqual(compare(value, value)["kind"], "same normalized image")

    def test_mirrored(self):
        original = fingerprint(encoded(sample()))
        flipped = fingerprint(encoded(ImageOps.mirror(sample())))
        self.assertEqual(compare(flipped, original)["kind"], "same normalized image (mirrored)")

    def test_resized_compressed_and_brightness(self):
        original = fingerprint(encoded(sample()))
        modified = ImageEnhance.Brightness(sample().resize((96, 96))).enhance(1.04)
        result = compare(fingerprint(encoded(modified, "JPEG", quality=85)), original)
        self.assertIsNotNone(result)
        self.assertIn("similar image", result["kind"])
        flipped = fingerprint(encoded(ImageOps.mirror(modified), "JPEG", quality=85))
        self.assertIn("mirrored", compare(flipped, original)["kind"])

    def test_unrelated_and_uniform_images(self):
        self.assertIsNone(compare(fingerprint(encoded(Image.new("RGB", (128, 128), "red"))), fingerprint(encoded(sample()))))
        # Uniform images share a gradient hash; the pixel check prevents this false match.
        self.assertIsNone(compare(fingerprint(encoded(Image.new("RGB", (128, 128), "red"))), fingerprint(encoded(Image.new("RGB", (128, 128), "blue")))))

    def test_invalid_and_oversized_images(self):
        for data in (b"not an image", b"x" * (2 * 1024 * 1024 + 1), encoded(Image.new("RGB", (1025, 1025)))):
            with self.assertRaises((ValueError, OSError)):
                fingerprint(data)
