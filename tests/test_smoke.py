import conet


def test_package_importable() -> None:
    assert conet.__version__ == "0.1.0"
