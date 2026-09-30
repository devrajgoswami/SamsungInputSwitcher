"""Control the input source of a Samsung Smart Monitor over the local network.

The Samsung M70D (LS32DM700U) does not implement DDC/CI, so the usual
`SetVCPFeature` approach cannot switch its input. Instead this package drives
the monitor's Tizen WebSocket remote API - the same one the SmartThings app
uses - to operate the on-screen Source list.
"""

from .config import Config, load_config
from .monitor import SamsungMonitor

__all__ = ["Config", "load_config", "SamsungMonitor"]
__version__ = "1.0.0"
