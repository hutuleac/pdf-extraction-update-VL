from io import BytesIO

import pytest
from PIL import Image

from extractor.errors import ResourceLimitError
from extractor.image_reader import extract_image
from extractor.ocr.raster import image_bytes_to_array


def test_image_pixel_breach_is_resource_limit():
    image = Image.new("RGB", (20, 20), "white")
    stream = BytesIO()
    image.save(stream, format="PNG")
    with pytest.raises(ResourceLimitError):
        image_bytes_to_array(stream.getvalue(), max_pixels=100)


def test_image_reader_does_not_downgrade_pixel_refusal(tmp_path, monkeypatch):
    path = tmp_path / "large.png"
    path.write_bytes(b"image")

    def refuse(*args, **kwargs):
        raise ResourceLimitError("image pixels", 101, 100)

    monkeypatch.setattr("extractor.ocr.raster.image_file_to_array", refuse)
    with pytest.raises(ResourceLimitError):
        extract_image(path)
