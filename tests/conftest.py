import os

# An example profile for the tests: one person, studio/1BR, Bronx excluded. Set before criteria is imported.
os.environ.setdefault("HW_INCOME", "150000")
os.environ.setdefault("HW_SIZES", "studio,1br")
os.environ.setdefault("HW_EXCLUDED_BOROUGHS", "Bronx")
