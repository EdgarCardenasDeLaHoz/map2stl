"""
core/height/predict -- Backward-compatibility shim.

Routes app.server.core.height.predict -> city2stl.height.predict so that
patch.object(predict_module, ...) works correctly in tests.
"""
import sys
import city2stl.height.predict as _impl

sys.modules[__name__] = _impl
