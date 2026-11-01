"""Moved to eligibility/criteria.py. This alias keeps old imports working until the files that use them are updated."""

import sys

from .eligibility import criteria

sys.modules[__name__] = criteria
