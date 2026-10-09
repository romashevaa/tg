#!/usr/bin/env python3
"""TradingView public idea timeline monitor. No credentials, no orders, no Gemini.
Usage: python tv_update_monitor.py --file tv_update_probe/eb5epG7p.html --id eb5epG7p
       python tv_update_monitor.py --url https://www.tradingview.com/chart/CHZUSDT.P/eb5epG7p-CHZUSDT-Update-Expected-to-be-following-BTC-Today/
       python tv_update_monitor.py --url https://www.tradingview.com/chart/CHZUSDT.P/eb5epG7p-CHZUSDT-Update-Expected-to-be-following-BTC-Today/ --watch 600
"""
import argparse, hashlib, html, json, re, sqlite3, sys, time, urllib.request, urllib.parse
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

class Timeline(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack=[]; self.current=None; self.rows=[]
    def handle_starttag(self, tag, attrs):
        a=dict(attrs); cls=a.get('class','')
        if tag=='div' and any(v.startswith('timelineItem-') for v in cls.split()) and self.current is None:
            self.current={'time':'','status':'','text':[],'images':[],'_depth':len(self.stack)}
        if self.current is not None:
            if tag=='relative-time':
                self.current['time']=a.get('event-time','')
            if tag=='time' and a.get('datetime'):
                self.current['time']=a['datetime']
            if tag=='img' and a.get('src'): self.current['images'].append(a['src'])
            if tag=='br' and self._inside('ast-'): self.current['text'].append('\n')
        self.stack.append((tag,cls))
    def _inside(self,prefix):
        return any(any(x.startswith(prefix) for x in cls.split()) for _,cls in self.stack)
    def handle_data(self,data):
        if self.current is None: return
        if self._inside('label-'): self.current['status']+=data
        elif self._inside('ast-'): self.current['text'].append(data)
    def handle_endtag(self,tag):
        if not self.stack: return
        # HTML here is well-formed enough; close tags corresponding to matching last open
        idx=next((i for i in range(len(self.stack)-1,-1,-1) if self.stack[i][0]==tag),-1)
        if idx<0:return
        self.stack=self.stack[:idx]
        if self.current is not None and len(self.stack)==self.current['_depth']:
            row=self.current; self.current=None
            row.pop('_depth',None)
            row['status']=' '.join(row['status'].split())
            row['text']=re.sub(r' *\n *','\n',''.join(row['text'])).strip()
            row['text']=re.sub(r'[ \t]{2,}',' ',row['text'])
            row['images']=list(dict.fromkeys(row['images']))
            if row['time'] and (row['text'] or row['status']):self.rows.append(row)
    def handle_startendtag(self,tag,attrs):
        self.handle_starttag(tag,attrs);self.handle_endtag(tag)

def parse(page):
    p = Timeline()
    p.feed(page)
    if p.rows:
        return p.rows

    from html.parser import HTMLParser

    class OriginalIdea(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack = []
            self.text = []
            self.date = ""
            self.collecting = False

        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            classes = a.get("class", "").split()

            if tag == "time" and a.get("datetime") and not self.date:
                self.date = a["datetime"]

            if not self.collecting and any(c.startswith("ast-") for c in classes):
                self.collecting = True
                self.stack = [tag]
            elif self.collecting:
                self.stack.append(tag)
                if tag == "br":
                    self.text.append("\\n")

        def handle_data(self, data):
            if self.collecting:
                self.text.append(data)

        def handle_endtag(self, tag):
            if self.collecting and self.stack:
                self.stack.pop()
                if not self.stack:
                    self.collecting = False

    original = OriginalIdea()
    original.feed(page)

    text = " ".join("".join(original.text).split())

    if original.date and text:
        return [{
            "time": original.date,
            "status": "",
            "text": text,
            "images": []
        }]

    return []

def get_page(url):
    parsed=urllib.parse.urlparse(url)
    if parsed.scheme!='https' or parsed.hostname not in ('tradingview.com','www.tradingview.com'):
        raise ValueError('Only HTTPS tradingview.com idea URLs are allowed')
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'text/html'})
    with urllib.request.urlopen(req,timeout=25) as r:
        if r.status !=200:raise RuntimeError(f'HTTP {r.status}')
        return r.read().decode('utf-8','replace')

def init_db(path):
    db=sqlite3.connect(path)
    db.execute('CREATE TABLE IF NOT EXISTS tv_events (idea TEXT NOT NULL, event_key TEXT NOT NULL, time_utc TEXT, status TEXT, body TEXT, images_json TEXT, discovered_utc TEXT, PRIMARY KEY (idea,event_key))')
    db.commit();return db

def scan(db,idea,page):
    rows=parse(page)
    if not rows:raise RuntimeError('NO_TIMELINE_FOUND: refusing to mark as checked')
    old=db.execute('SELECT COUNT(*) FROM tv_events WHERE idea=?',(idea,)).fetchone()[0]
    new=[]
    for row in rows:
        identity=json.dumps([row['time'],row['status'],row['text'],row['images']],ensure_ascii=False)
        key=hashlib.sha256(identity.encode()).hexdigest()
        with db:
            cur=db.execute('INSERT OR IGNORE INTO tv_events VALUES (?,?,?,?,?,?,?)',(idea,key,row['time'],row['status'],row['text'],json.dumps(row['images']),datetime.now(timezone.utc).isoformat()))
        if cur.rowcount:new.append(row)
    print(f'{idea}: timeline={len(rows)} new={len(new)} previous={old} '+('BASELINE' if old==0 else ''))
    for row in new:
        print('  '+row['time']+' | '+(row['status'] or 'IDEA / UPDATE')+' | '+row['text'][:240].replace('\n',' / '))
    return rows,new

def main():
    a=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument('--url',action='append',default=[],help='Full public idea URL; may be used repeatedly')
    a.add_argument('--file',type=Path);a.add_argument('--id')
    a.add_argument('--db',default='tv_updates_monitor.sqlite');a.add_argument('--watch',type=int,default=0,metavar='SECONDS')
    args=a.parse_args()
    if not args.url and not args.file:a.error('provide --url or --file')
    if args.file and not args.id:a.error('--file requires --id')
    if args.watch and args.watch<300:a.error('minimum watch interval is 300 seconds')
    db=init_db(args.db)
    while True:
        tasks=([(args.id,args.file.read_text(encoding='utf-8'),None)] if args.file else [(re.search(r'/chart/[^/]+/([A-Za-z0-9]{8})(?:-|/)',u).group(1),None,u) for u in args.url])
        for idea,page,url in tasks:
            try:scan(db,idea,page if page is not None else get_page(url))
            except Exception as exc:print(f'ERROR {idea}: {exc}',file=sys.stderr)
        if not args.watch:break
        time.sleep(args.watch)
if __name__=='__main__':main()
