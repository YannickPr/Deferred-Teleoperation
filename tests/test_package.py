from deferred_teleop import PROTOCOL_VERSION, __version__


def test_public_versions_are_explicit() -> None:
    assert __version__ == "0.1.0"
    assert PROTOCOL_VERSION == "dtt/0"
