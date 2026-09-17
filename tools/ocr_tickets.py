"""Offline OCR into reviewable ticket tasks. Never invent an answer key."""
import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image, ImageOps


def lines(image):
    image=image.convert('L');width,height=image.size
    pixels=image.load();scores=[]
    for y in range(height):
        scores.append(sum(pixels[x,y]<180 for x in range(int(width*.05),int(width*.96))))
    counts=scores
    scores=[counts[y]-(counts[max(0,y-4)]+counts[min(height-1,y+4)])/2 for y in range(height)]
    candidates=[y for y,value in enumerate(scores) if value>width*.35]
    groups=[]
    for y in candidates:
        if not groups or y-groups[-1][-1]>8: groups.append([y])
        else: groups[-1].append(y)
    return [max(group,key=lambda y:scores[y]) for group in groups],sum(sorted(scores,reverse=True)[:20])


def process_page(path):
    page=int(path.stem.split('-')[-1]);original=Image.open(path).convert('L')
    small=original.resize((668,round(original.height*668/original.width)))
    best_angle=0
    grid,_=lines(small)
    grid=[round(y*original.width/668) for y in grid]
    image=original
    # Four separators are sufficient: header bottom and the three task bottoms.
    # Contrast against neighbouring rows rejects dense text and scanner creases.
    reliable=len(grid)>=4
    boundaries=grid[-4:] if reliable else []
    def ocr(crop,name):
        location=path.parent/f'{path.stem}-{name}.png';crop.save(location)
        return subprocess.check_output(['tesseract',str(location),'stdout','-l','rus+eng','--psm','6'],stderr=subprocess.DEVNULL,text=True).strip()
    tasks=[]
    if reliable:
        pixels=image.load()
        vertical=[sum(pixels[x,y]<180 for y in range(boundaries[0],boundaries[-1])) for x in range(image.width)]
        def edge(low,high):
            return max(range(round(image.width*low),round(image.width*high)),key=lambda x:vertical[x]-(vertical[max(0,x-5)]+vertical[min(image.width-1,x+5)])/2)
        left,middle,right=edge(.10,.16),edge(.46,.53),edge(.90,.97)
        for number in range(1,4):
            top,bottom=boundaries[number-1],boundaries[number]
            caller=ocr(image.crop((left+6,top+5,middle-6,bottom-5)),f'{number}-situation')
            address=ocr(image.crop((middle+6,top+5,right-6,bottom-5)),f'{number}-address')
            tasks.append({'page':page,'ticket':page,'number':number,'situation':caller,'address':address,
                          'needs_review':True,'segmentation':'table-lines','deskew_degrees':best_angle})
    else:
        raw=ocr(image,'full')
        tasks=[{'page':page,'ticket':page,'number':0,'situation':raw,'address':'','needs_review':True,
                'segmentation':'page-only: check table manually','deskew_degrees':best_angle}]
    return tasks


def main():
    parser=argparse.ArgumentParser();parser.add_argument('pdf');parser.add_argument('output');parser.add_argument('--images')
    args=parser.parse_args();source=Path(args.pdf)
    with tempfile.TemporaryDirectory() as directory:
        images=Path(args.images) if args.images else Path(directory)
        if not args.images: subprocess.run(['pdftoppm','-r','170','-png',str(source),str(images/'ticket')],check=True)
        pages=sorted(images.glob('ticket-[0-9][0-9].png'))
        if not pages: raise SystemExit('Нет страниц для распознавания')
        with ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(process_page,pages))
        payload={'source_file':source.name,'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
                 'pages':len(pages),'tasks':[task for result in results for task in result],
                 'note':'Распознано локально. Эталон и службы не назначаются автоматически. Требуется проверка преподавателем по оригиналу.'}
        output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(payload,ensure_ascii=False,indent=2))
        print(json.dumps({'pages':len(pages),'tasks':len(payload['tasks']),'page_only':sum(t['number']==0 for t in payload['tasks'])},ensure_ascii=False))
if __name__=='__main__': main()
