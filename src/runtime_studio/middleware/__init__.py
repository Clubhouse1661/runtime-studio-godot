"""FastMCP middleware for the Runtime Studio for Godot server."""

from __future__ import annotations

from runtime_studio.middleware.client_wrapper_kwargs import (
    CLIENT_WRAPPER_KWARGS,
    StripClientWrapperKwargs,
)
from runtime_studio.middleware.godot_command_error import PreserveGodotCommandErrorData
from runtime_studio.middleware.op_typo_hint import HintOpTypoOnManage
from runtime_studio.middleware.parse_stringified_params import ParseStringifiedParams

__all__ = [
    "CLIENT_WRAPPER_KWARGS",
    "HintOpTypoOnManage",
    "ParseStringifiedParams",
    "PreserveGodotCommandErrorData",
    "StripClientWrapperKwargs",
]
