import io
import time
from pathlib import Path

import pytest
from PIL import Image

from server.config import Config
from server.db import connect
from server.identify import IdentifyResult


@pytest.fixture
def cfg(tmp_path):
    return Config(data_dir=tmp_path / "explora-data")


@pytest.fixture
def conn(cfg):
    connection = connect(cfg)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def jpeg():
    def make_jpeg(size=(64, 64), seed=1, exif_dt=None, gps=False):
        # Different seed values create different image content.
        image = Image.new(
            "RGB",
            size,
            color=((seed * 47) % 256, (seed * 83) % 256, (seed * 113) % 256),
        )
        exif = Image.Exif()
        if exif_dt:
            exif[36867] = exif_dt  # DateTimeOriginal
            exif[306] = exif_dt    # DateTime
        if gps:
            exif[34853] = {0: b"\x02\x03\x00\x00"}
        output = io.BytesIO()
        image.save(output, format="JPEG", exif=exif)
        return output.getvalue()

    return make_jpeg


@pytest.fixture
def fake():
    def make_result(
        status="ok",
        category="bird",
        identification="Blue Jay",
        confidence="High",
        explanation="A bird with blue plumage.",
        fact="Birds have feathers.",
        error=None,
    ):
        return IdentifyResult(
            status=status,
            category=category,
            identification=identification,
            confidence=confidence,
            explanation=explanation,
            fact=fact,
            model="test-model",
            settings={"max_side": 768, "think": False},
            prompt_hash="test-prompt-hash",
            elapsed_s=0.1,
            load_s=0.0,
            raw="{}",
            error=error,
        )

    return make_result
