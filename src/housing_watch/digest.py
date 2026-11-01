"""Moved to notify/digest.py. This alias keeps old imports working until the files that use them are updated."""

import sys

from .notify import digest

sys.modules[__name__] = digest
