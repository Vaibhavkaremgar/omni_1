from __future__ import annotations

from io import BytesIO
from pathlib import PurePath
from zipfile import BadZipFile, ZipFile
import re
import xml.etree.ElementTree as ET


SUPPORTED_INPUT_EXTENSIONS = {".pdf", ".txt", ".md", ".docx"}


def to_pdf(content: bytes, filename: str) -> tuple[bytes, str]:
    extension = PurePath(filename).suffix.casefold()
    if extension == ".pdf":
        if not content.startswith(b"%PDF"):
            raise ValueError("The uploaded PDF is invalid.")
        return content, filename if filename.casefold().endswith(".pdf") else f"{filename}.pdf"
    if extension not in SUPPORTED_INPUT_EXTENSIONS:
        raise ValueError("Only PDF, DOCX, TXT, and Markdown files can be converted to Knowledge Base PDF format. Legacy .doc files are not supported.")
    if extension == ".docx":
        try:
            with ZipFile(BytesIO(content)) as archive:
                xml = archive.read("word/document.xml")
            root = ET.fromstring(xml)
            text = "\n".join(
                "".join(node.text or "" for node in paragraph.iter() if node.tag.endswith("}t"))
                for paragraph in root.iter() if paragraph.tag.endswith("}p")
            )
        except (BadZipFile, KeyError, ET.ParseError) as exc:
            raise ValueError("The DOCX file could not be read.") from exc
    else:
        text = content.decode("utf-8", errors="replace")
    return render_text_pdf(text), f"{PurePath(filename).stem}.pdf"


def render_text_pdf(text: str) -> bytes:
    lines = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        lines.extend(raw[i:i + 95] for i in range(0, max(len(raw), 1), 95))
    lines = lines or [""]
    pages = [lines[i:i + 48] for i in range(0, len(lines), 48)]
    objects: list[bytes] = []
    def add(value: str) -> int:
        objects.append(value.encode("latin-1", errors="replace")); return len(objects)
    catalog = add("<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add("")
    font_id = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    page_ids = []
    for page in pages:
        stream = "BT /F1 10 Tf 50 750 Td 13 TL " + " ".join(f"({re.sub(r'([\\()])', r'\\\\\\1', line)}) Tj T*" for line in page) + " ET"
        stream_id = add(f"<< /Length {len(stream.encode('latin-1', errors='replace'))} >>\nstream\n{stream}\nendstream")
        page_ids.append(add(f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {stream_id} 0 R >>"))
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{' '.join(f'{i} 0 R' for i in page_ids)}] /Count {len(page_ids)} >>".encode()
    output = bytearray(b"%PDF-1.4\n"); offsets = [0]
    for index, obj in enumerate(objects, 1): offsets.append(len(output)); output.extend(f"{index} 0 obj\n".encode()); output.extend(obj); output.extend(b"\nendobj\n")
    start = len(output); output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()); output.extend("".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:]).encode()); output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{start}\n%%EOF".encode()); return bytes(output)
