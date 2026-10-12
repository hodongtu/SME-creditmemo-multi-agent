"""A CSV must come back split on its real delimiter."""

import tempfile
import unittest
from pathlib import Path

from src.utils.reading.extractors import extract_csv_text


def _write(text: str, newline: str) -> str:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".csv", delete=False, encoding="utf-8", newline=newline
    )
    handle.write(text)
    handle.close()
    return handle.name


class CsvDelimiterTest(unittest.TestCase):
    def tearDown(self) -> None:
        Path(self.path).unlink(missing_ok=True)

    def test_a_windows_file_is_not_split_on_its_line_endings(self) -> None:
        # Opened with newline="", every line keeps its "\\r", and the sniffer
        # used to choose it as the delimiter: "bad delimiter value".
        self.path = _write(
            '"Tài liệu mẫu, dữ liệu giả định, tạo tự động để kiểm thử"\n'
            "Chỉ tiêu,Mã số,Năm nay\nDoanh thu,01,1.210.969.204\n",
            newline="\r\n",
        )
        text = extract_csv_text(self.path)
        self.assertIn("Doanh thu\t01\t1.210.969.204", text)

    def test_a_company_name_is_not_split_on_its_spaces(self) -> None:
        self.path = _write(
            "CÔNG TY TNHH AN CƯỜNG FARM,,,,\n"
            "Ngày,Chứng từ,Diễn giải,Nợ,Có\n"
            "05/01/2025,PT01,Thu tiền,1.000.000,0\n",
            newline="\n",
        )
        first = extract_csv_text(self.path).splitlines()[0]
        self.assertTrue(first.startswith("CÔNG TY TNHH AN CƯỜNG FARM"), first)


if __name__ == "__main__":
    unittest.main()
