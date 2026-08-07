import ctypes
import unittest
from unittest.mock import Mock, patch

from codex_monitor.auto_resume import _Input, _read_input_value


class AutoResumeInteropTests(unittest.TestCase):
    def test_input_structure_matches_windows_abi(self) -> None:
        # Win32 INPUT is 40 bytes on 64-bit Windows and 28 bytes on 32-bit.
        expected_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(_Input), expected_size)

    @patch("pywinauto.Desktop")
    def test_prosemirror_placeholder_is_treated_as_empty(self, desktop_type: Mock) -> None:
        edit = Mock()
        edit.element_info.control_type = "Edit"
        edit.element_info.class_name = "ProseMirror"
        edit.element_info.name = "Submit a follow-up"
        edit.get_value.return_value = "\nSubmit a follow-up"
        desktop_type.return_value.from_point.return_value = edit

        self.assertEqual(_read_input_value(10, 10), "")


if __name__ == "__main__":
    unittest.main()
