"""Retained AI agent conversations for chat interfaces."""
__version__ = "0.1.0b0"

from .client import Client, Connection
from .conversation import Conversation
from .agents import AgentEnvironments
from .launcher import StandaloneLauncher
from .managed_launcher import WorkerLauncher, ProcessOwner
from .controller import NativeController, ControllerSpec
from .mcp import LiveMcpConnector
from .models import (PreparedLaunch, ConnectionRef, SessionRef, OperationReceipt,
                     ConversationError, Launcher, Connector, TransportHandle, operation_key)
from .transport import LocalConnector
from .native_codex import NativeCodexConnection, NativeCodexError
from .support import native_startup_support

# Descriptive frontend entry point; Client remains the same public implementation.
ConversationClient = Client

__all__ = ["Client", "ConversationClient", "Connection", "Conversation", "AgentEnvironments", "PreparedLaunch", "ConnectionRef", "SessionRef",
           "OperationReceipt", "ConversationError", "Launcher", "Connector", "TransportHandle",
           "StandaloneLauncher", "WorkerLauncher", "ProcessOwner", "NativeController", "ControllerSpec", "operation_key", "LocalConnector", "LiveMcpConnector", "NativeCodexConnection", "NativeCodexError", "native_startup_support"]
