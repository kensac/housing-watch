"""Moved to notify/changes.py. This alias keeps old imports working until the files that use them are updated."""

import sys

from .notify import changes

sys.modules[__name__] = changes
