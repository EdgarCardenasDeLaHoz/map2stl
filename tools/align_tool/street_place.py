"""Place a plate on the map by its streets -- CLI shim.

The placement moved to ``city2stl.registration.street_place`` (2026-09-27) so the web app's
plate-registration panel can run it without shelling out; read that module's docstring for
how it works.  ``python street_place.py [slug ...] [--hide M] [--channels ...]`` still runs
the batch CLI, and ``import street_place`` (export_align_data's placement hook) binds the
promoted module itself.
"""
import sys

from city2stl.registration import street_place as _impl

if __name__ == "__main__":
    _impl.main()
else:
    sys.modules[__name__] = _impl
