"""Read-only, bounded public TradingView idea discovery. Stops on access restrictions.

The author-filtered feed endpoint was verified from a browser response on 2026-10-08.
It is not a documented or guaranteed stable integration; no access-control evasion.
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timezone, timedelta
import httpx
from .tv_registry import parse_profile, parse_idea

class ScanBlocked(RuntimeError):
    pass

def parse_feed_page(data, handle, cutoff):
    if not isinstance(data,dict) or not isinstance(data.get('results'),list):
        raise ValueError('TradingView feed schema changed')
    ideas={}
    for row in data['results']:
        if not isinstance(row,dict):continue
        owner=row.get('user') or {}
        if not isinstance(owner,dict) or owner.get('username','').casefold()!=handle.casefold():continue
        if not row.get('is_public',True) or not row.get('is_visible',True):continue
        stamp=row.get('created_at') or row.get('date_timestamp')
        try:
            when=(datetime.fromtimestamp(stamp,timezone.utc) if isinstance(stamp,(int,float))
                  else datetime.fromisoformat(stamp.replace('Z','+00:00')))
            if when.tzinfo is None:when=when.replace(tzinfo=timezone.utc)
        except (ValueError,TypeError,OverflowError,AttributeError):continue
        if when<cutoff:continue
        link=row.get('chart_url')
        if not link:continue
        try:iid,url=parse_idea(link)
        except (ValueError,TypeError):continue
        ideas[iid]=(url,when,True)
    return ideas

async def discover(profile,days=365,on_page=None,max_pages=100,pause=3,max_ideas=2500):
    handle,_=parse_profile(profile)
    cutoff=datetime.now(timezone.utc)-timedelta(days=days)
    found={}; metadata={}; notes=[]; pages=0; reported_count=None; page_count=None; excluded_old=0; excluded_other=0
    url='https://www.tradingview.com/api/v1/ideas/'
    headers={'Accept':'application/json','X-Requested-With':'XMLHttpRequest'}
    async with httpx.AsyncClient(timeout=25,follow_redirects=True,headers=headers) as client:
        for page in range(1,max_pages+1):
            if page>1:await asyncio.sleep(pause)
            try:
                resp=await client.get(url,params={'page':page,'per_page':24,'by':handle,'locale':'en','q':''})
            except (httpx.HTTPError,asyncio.TimeoutError) as exc:
                notes.append(f'Network error on page {page}: {type(exc).__name__}');break
            pages+=1
            if resp.status_code in (401,403,429):raise ScanBlocked(f'HTTP {resp.status_code}: TradingView access restricted; stopped')
            if resp.status_code!=200:
                notes.append(f'Page {page}: HTTP {resp.status_code}');break
            try:
                data=resp.json()
                current=parse_feed_page(data,handle,cutoff)
            except (ValueError,TypeError,KeyError) as exc:
                notes.append(f'Page {page}: invalid JSON/schema: {exc}');break
            reported_count=data.get('count')
            page_count=data.get('page_count')
            found.update(current)
            for row in data['results']:
                if not isinstance(row,dict):continue
                owner=row.get('user') or {}
                if (owner.get('username') or '').casefold()!=handle.casefold():
                    excluded_other+=1;continue
                try:
                    iid, _=parse_idea(row.get('chart_url',''))
                except (ValueError,TypeError):continue
                if iid not in current:
                    excluded_old+=1;continue
                metadata[iid]={'title':row.get('name') or '', 'market':(row.get('symbol') or {}).get('type') or '',
                               'owner':owner.get('username') or ''}
            if on_page:await on_page(page,len(found))
            if len(found)>=max_ideas:
                notes.append(f'Safety cap at {max_ideas} ideas');break
            if not data['results'] or not data.get('next'):
                break
            # Created date need not be sorted due to edits: don't stop just for an old item.
            if isinstance(page_count,int) and page>=page_count:break
        else:notes.append(f'Max page limit {max_pages} reached')
    if not found:notes.append('No matching author ideas in returned feed; verify public access and date range')
    return {'handle':handle,'ideas':found,'pages':pages,'cutoff':cutoff,'notes':notes,
            'reported_count':reported_count,'reported_pages':page_count,'metadata':metadata,'excluded_old':excluded_old,'excluded_other':excluded_other,
            'coverage':'UNVERIFIED — visibility, deletions, edits, and endpoint limits may omit ideas'}
