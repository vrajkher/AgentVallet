"""AgentVallet: portable AI work, memory and skill brain."""

__version__ = "0.1.0"
SPEC_VERSION = "1.0"

__all__ = ["AgentVallet", "Settings", "SPEC_VERSION", "__version__"]


def __getattr__(name: str):  # lazy imports keep `import agentvallet` cheap
    if name == "AgentVallet":
        from .core import AgentVallet

        return AgentVallet
    if name == "Settings":
        from .config import Settings

        return Settings
    raise AttributeError(name)
