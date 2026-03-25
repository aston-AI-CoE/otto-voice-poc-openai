"""
Minimal stub of claude_agent_sdk for the otto-voice-poc-openai server.
Only implements what otto.worker.core.local_mcp needs.
"""

class SdkMcpTool:
    def __init__(self, handler):
        self.handler = handler

def tool(name, description, params):
    """Returns a decorator that wraps tool_func in an SdkMcpTool."""
    def decorator(tool_func):
        return SdkMcpTool(handler=tool_func)
    return decorator

def create_sdk_mcp_server(name, version, tools):
    return None

class HookMatcher:
    pass
