"""Errors raised by the local Blender subprocess."""

class WorkerError(RuntimeError):
    pass


class RenderCancelled(RuntimeError):
    """A local render stopped after its cancellation callback was set."""
