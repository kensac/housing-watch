"""Moved to eligibility/ami.py. This alias keeps old imports working until the files that use them are updated."""

import sys

from .eligibility import ami

sys.modules[__name__] = ami
