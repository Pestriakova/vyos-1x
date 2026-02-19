import glob
import xml.etree.ElementTree as ET

TARGET_TAGS = {"node", "leafNode", "tagNode"}

total = 0
with_help = 0
bad_files = 0

files = glob.glob("interface-definitions/**/*.xml.in", recursive=True)

for path in files:
    try:
        tree = ET.parse(path)
        root = tree.getroot()
    except Exception:
        bad_files += 1
        continue

    for elem in root.iter():
        if elem.tag in TARGET_TAGS:
            total += 1
            props = elem.find("properties")
            if props is not None and props.find("help") is not None:
                with_help += 1

coverage = (with_help / total * 100) if total else 0

print("Files scanned:", len(files))
print("Files skipped (parse issues):", bad_files)
print("Total CLI entities:", total)
print("Entities with <help>:", with_help)
print("Help Coverage (%):", round(coverage, 2))
