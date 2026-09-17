import json,time,threading,hashlib
from pathlib import Path
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer
from fastapi import FastAPI,HTTPException
from pydantic import BaseModel,Field
app=FastAPI(title='ARM112 local neural assessment')
manifest=json.loads(Path('/model/manifest.json').read_text())
if hashlib.sha256(Path('/model/onnx/model.onnx').read_bytes()).hexdigest()!=manifest['sha256']['onnx/model.onnx']:raise RuntimeError('Model checksum mismatch')
options=ort.SessionOptions();options.intra_op_num_threads=2
model=ort.InferenceSession('/model/onnx/model.onnx',sess_options=options,providers=['CPUExecutionProvider'])
tokenizer=Tokenizer.from_file('/model/tokenizer.json');tokenizer.enable_truncation(max_length=128,stride=32)
lock=threading.Lock()
class Pair(BaseModel):
    expected:str=Field(max_length=10000)
    actual:str=Field(max_length=10000)
@app.get('/health')
def health():return {'status':'ok',**manifest,'provider':'CPUExecutionProvider','algorithm':'reference-chunk-coverage-v1'}
def embeddings(encodings):
    vectors=[]
    for offset in range(0,len(encodings),8):
        batch=encodings[offset:offset+8];width=max(len(x.ids) for x in batch)
        feeds={name:np.zeros((len(batch),width),dtype=np.int64) for name in ['input_ids','attention_mask','token_type_ids']}
        for index,item in enumerate(batch):
            size=len(item.ids);feeds['input_ids'][index,:size]=item.ids;feeds['attention_mask'][index,:size]=item.attention_mask;feeds['token_type_ids'][index,:size]=item.type_ids
        output=model.run(None,{x.name:feeds[x.name] for x in model.get_inputs()})[0];mask=feeds['attention_mask'][...,None]
        pooled=(output*mask).sum(axis=1)/np.maximum(mask.sum(axis=1),1);pooled/=np.maximum(np.linalg.norm(pooled,axis=1,keepdims=True),1e-12);vectors.extend(pooled)
    return np.array(vectors)
@app.post('/similarity')
def similarity(pair:Pair):
    start=time.monotonic();counts=[0,0]
    if not pair.expected.strip() or not pair.actual.strip():score=0.0
    else:
        with lock:batch=tokenizer.encode_batch([pair.expected,pair.actual])
        groups=[[x,*x.overflowing] for x in batch];counts=[len(x) for x in groups]
        if max(counts)>128:raise HTTPException(422,'Text exceeds assessment capacity')
        reference=embeddings(groups[0]);actual=embeddings(groups[1]);score=float(np.clip((reference@actual.T).max(axis=1).mean(),0,1))
    return {'similarity':round(score,6),'model':manifest['model'],'revision':manifest['revision'],'elapsed_ms':round((time.monotonic()-start)*1000),'max_tokens_per_chunk':128,'chunks':counts,'algorithm':'reference-chunk-coverage-v1'}
