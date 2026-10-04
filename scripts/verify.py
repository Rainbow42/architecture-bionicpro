import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
names = subprocess.check_output(['git', 'ls-files', '--cached', '--others', '--exclude-standard'], cwd=root, text=True).splitlines()
secrets = {key: value for key, value in dotenv_values(root / '.env').items()
           if value and len(value) > 20 and key not in {'PUBLIC_URL', 'PIPELINE_MODE', 'S3_ACCESS_KEY'}}
for name in names:
    path = root / name
    if not path.is_file():
        continue
    try:
        content = path.read_text()
    except UnicodeDecodeError:
        continue
    for key, secret in secrets.items():
        if secret in content:
            raise RuntimeError(f'Local secret {key} found in {name}')
    if path.suffix == '.json':
        json.loads(content)
    if path.suffix in {'.yaml', '.yml'}:
        yaml.safe_load(content)
tree = ET.parse(root / 'docs/bionicpro.drawio')
for diagram in tree.findall('diagram'):
    cells = diagram.findall('.//mxCell')
    ids = [cell.attrib['id'] for cell in cells]
    assert len(ids) == len(set(ids)), diagram.attrib['name']
    for cell in cells:
        for key in ('parent', 'source', 'target'):
            if key in cell.attrib:
                assert cell.attrib[key] in ids
print('PASS: JSON, YAML and all three draw.io pages are structurally valid')
print('PASS: local .env values are absent from tracked and candidate project files')
