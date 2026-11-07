"""No Django models: the trail's tables are plain SQL (migrations/0001_initial.py).

This module exists because Django sends post_migrate only to apps that have one, and that
signal installs the triggers (apps.py).
"""
