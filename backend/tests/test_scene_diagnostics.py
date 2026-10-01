import unittest
from app.scene_diagnostics import log_scene_action
from app.models import Surface, SurfaceAction

class SceneDiagnosticsTests(unittest.TestCase):
    def test_logs_counts_and_action_category_without_private_data(self):
        surface = Surface(kind="list", title="PRIVATE TITLE", subtitle="PRIVATE SUBTITLE", items=[{"label":"PRIVATE REMINDER"}], actions=[SurfaceAction(id="dismiss", label="PRIVATE LABEL")])
        with self.assertLogs("uvicorn.error", level="INFO") as capture:
            log_scene_action("test-device", "reminder:12345", surface)
        value = capture.output[0]
        self.assertIn("action=reminder", value)
        self.assertIn("cards=1 buttons=1", value)
        self.assertNotIn("PRIVATE", value)
        self.assertNotIn("12345", value)

    def test_unknown_action_is_never_echoed(self):
        with self.assertLogs("uvicorn.error", level="INFO") as capture:
            log_scene_action("test-device", "PRIVATE_PAYLOAD", None)
        self.assertIn("action=invalid", capture.output[0])
        self.assertNotIn("PRIVATE_PAYLOAD", capture.output[0])
