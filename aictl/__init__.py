# AI Native Linux OS — aictl package
#
# Apple-philosophy SDK entry point:
#     from aictl import ai
#     answer = ai.ask("What is 2+2?")
#
# That's the whole developer contract. Everything else is progressive disclosure.

from aictl.core.constants import AICTL_VERSION as __version__

# The invisible infrastructure surface
from aictl.sdk import ai

__all__ = ["ai", "__version__"]
