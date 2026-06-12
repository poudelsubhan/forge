"""Forge — a self-extending agent harness.

The agent, on hitting a capability gap, authors a new tool plus a test for that
tool, runs the test in a sandbox, and only promotes the tool into its registry
on a pass. No tool enters the registry without passing its own test — the
verification gate is the product.
"""

__version__ = "0.1.0"
