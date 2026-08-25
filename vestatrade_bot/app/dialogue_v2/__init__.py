"""Shadow dialogue architecture introduced during the staged V2 rollout.

Imports stay intentionally lazy.  ``SessionState`` references the contracts,
while the controller references the Stage 1 interpreter; eager re-exports here
would create a model/interpreter import cycle.
"""
