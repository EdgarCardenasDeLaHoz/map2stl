"""Extract key pages from the latest Cartagena skyline report PDF."""
import fitz
import pathlib
import sys

pdf_path = pathlib.Path(
    "city2stl/skyline_cv/runs/region_reports/Miami_skyline_report.pdf")
out = pathlib.Path("city2stl/skyline_cv/runs/region_reports")

pdf = fitz.open(str(pdf_path))
n = pdf.page_count
print(f"PDF has {n} pages")
if n == 0:
    print("ERROR: 0 pages")
    sys.exit(1)

# Pages: 1=summary, 2=map, 3-13=screened points (11 total), rest=registrations, last=validation
targets = list(range(min(13, n))) + ([n - 1] if n > 13 else [])
for i in targets:
    pix = pdf[i].get_pixmap(dpi=96)
    out_path = out / f"pg_{i+1:02d}.png"
    pix.save(str(out_path))
    print(f"  saved page {i+1} -> {out_path.name}")

pdf.close()
print("Done")
