"""Document text extraction helpers."""

import csv
from pathlib import Path

import pandas as pd

from src.utils.ocr import ocr_pdf


def extract_csv_text(
    csv_path: str,
    max_rows: int = 500,
    max_chars: int = 100000,
) -> str:
    """Extract CSV content as tab-separated text for LLM analysis."""
    encodings = ["utf-8", "latin-1"]
    last_error = None

    for encoding in encodings:
        try:
            with open(csv_path, newline="", encoding=encoding) as csv_file:
                sample = csv_file.read(4096)
                csv_file.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample)
                except csv.Error:
                    dialect = csv.excel

                rows = []
                for row_index, row in enumerate(csv.reader(csv_file, dialect)):
                    if row_index >= max_rows:
                        rows.append([f"... truncated after {max_rows} rows ..."])
                        break
                    rows.append([cell.strip() for cell in row])

            content = "\n".join("\t".join(row) for row in rows)
            return content[:max_chars]
        except UnicodeDecodeError as exc:
            last_error = exc

    raise ValueError(f"Unable to decode CSV file: {last_error}")


def extract_excel_text(excel_path: str) -> str:
    """Extract XLS/XLSX workbook content as text grouped by sheet."""
    engine = "openpyxl" if Path(excel_path).suffix.lower() == ".xlsx" else "xlrd"
    workbook = pd.read_excel(
        excel_path,
        sheet_name=None,
        dtype=str,
        engine=engine,
    )

    sheet_blocks = []
    for sheet_name, dataframe in workbook.items():
        dataframe = dataframe.fillna("")
        sheet_text = dataframe.to_csv(sep="\t", index=False)
        sheet_blocks.append(f"--- Sheet: {sheet_name} ---\n{sheet_text}")

    return "\n\n".join(sheet_blocks)


def extract_document_text(
    file_path: str,
    ocr_timeout_seconds: float | None = None,
) -> str:
    """Extract text from supported uploaded documents."""
    spreadsheet_extensions = {".csv", ".xls", ".xlsx"}

    extension = Path(file_path).suffix.lower()
    if extension == ".pdf":
        return ocr_pdf(file_path, timeout_seconds=ocr_timeout_seconds)
    if extension == ".csv":
        return extract_csv_text(file_path)
    if extension in spreadsheet_extensions - {".csv"}:
        return extract_excel_text(file_path)
    raise ValueError(f"Unsupported file extension: {extension}")
