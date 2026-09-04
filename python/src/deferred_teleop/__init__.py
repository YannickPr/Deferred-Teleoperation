"""Public Python namespace for Deferred Teleoperation."""

from deferred_teleop.protocol import MessageEnvelope

__all__ = ["MessageEnvelope", "PROTOCOL_VERSION", "__version__"]

__version__ = "0.0.0"
PROTOCOL_VERSION = "dtt/0"
