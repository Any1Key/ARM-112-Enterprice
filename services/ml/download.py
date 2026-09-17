import json, hashlib
from pathlib import Path
from huggingface_hub import HfApi, snapshot_download
repo='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
revision='e8f8c211226b894fcb81acc59f3b34ba3efd5f42'
snapshot_download(repo,revision=revision,local_dir='/model',allow_patterns=['onnx/model.onnx','tokenizer.json','tokenizer_config.json','special_tokens_map.json','config.json','README.md'])
files={str(p.relative_to('/model')):hashlib.sha256(p.read_bytes()).hexdigest() for p in Path('/model').rglob('*') if p.is_file() and '.cache' not in p.parts}
Path('/model/manifest.json').write_text(json.dumps({'model':repo,'revision':revision,'sha256':files}))
