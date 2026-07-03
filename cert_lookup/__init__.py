"""cert_lookup: UI-agnostic core for driving PSA cert lookups across CardLadder and Alt.

The interface layer (CLI today, web/Mac app later) should import LookupController and call
`await controller.start()` once, then `await controller.run(cert)` per cert.
"""

from .controller import LookupController

__all__ = ["LookupController"]
