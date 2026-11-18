"""Number format for the model: bfloat16 only where the GPU does it natively (compute capability 8+)."""

import pytest

from docintel.engines.nemotron_gpu import choose_dtype


@pytest.mark.parametrize(
    ("capability", "expected"),
    [(None, "float32"), ((7, 5), "float16"), ((8, 0), "bfloat16"), ((9, 0), "bfloat16")],
)
def test_the_format_follows_what_the_device_supports(capability, expected):
    assert choose_dtype(capability) == expected
