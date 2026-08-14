from backend.app import main


def test_app_importable():
    assert getattr(main, "app", None) is not None
