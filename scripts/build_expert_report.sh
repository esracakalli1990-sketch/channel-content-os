#!/usr/bin/env bash
# Tek komutla rapor + ekler. Veri seti önce export-dataset ile tazelenmeli.
set -e
ROOT=/home/user/channel-content-os
TMP=$(mktemp -d)
python3 "$ROOT/scripts/expert_brief.py"    "$ROOT/data/expert_dataset.json" "$TMP/a.pdf" >/dev/null
python3 "$ROOT/scripts/expert_appendix.py" "$ROOT/data/expert_dataset.json" "$TMP/b.pdf" >/dev/null
python3 - "$TMP/a.pdf" "$TMP/b.pdf" "$ROOT/Unfoldables_Kanal_Raporu.pdf" <<'PY'
import sys
from pypdf import PdfReader, PdfWriter
w = PdfWriter()
for src in sys.argv[1:-1]:
    for page in PdfReader(src).pages:
        w.add_page(page)
w.add_metadata({"/Title": "Unfoldables — Kanal Durum Raporu ve Ekler",
                "/Author": "channel-content-os"})
with open(sys.argv[-1], "wb") as fh:
    w.write(fh)
print(sys.argv[-1])
PY
rm -rf "$TMP"
