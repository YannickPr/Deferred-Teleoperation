from deferred_teleop import PROTOCOL_VERSION, __version__


def test_public_versions_are_explicitly_experimental() -> None:
    assert __version__ == "0.0.0"
    assert PROTOCOL_VERSION == "dtt/0"
