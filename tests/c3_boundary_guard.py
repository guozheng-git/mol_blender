"""C3 guard loaded by pytest before test/application imports.

Boundary replacements belong to each contract test. The inherited audit fails
the whole run even when application code catches a forbidden boundary attempt.
"""
from c1_boundary_guard import pytest_collection_modifyitems, pytest_sessionfinish
