import urllib.request,re
def get(u):
    return urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.dukascopy.com/'}),timeout=25).read().decode('utf8','ignore')
seen=set()
for u in ['https://widgets.dukascopy.com/embed/embed.js','https://widgets.dukascopy.com/en/']:
    t=get(u); print('len',u,len(t))
    for m in sorted(set(re.findall(r'["\']([^"\']*(?:sentiment|freeserv|path=)[^"\']*)["\']',t,re.I)))[:30]: print('HIT',m[:200])
    for js in re.findall(r'src=["\']([^"\']+\.js[^"\']*)',t)[:15]:
        if js in seen: continue
        seen.add(js); ju=js if js.startswith('http') else 'https://widgets.dukascopy.com'+('' if js.startswith('/') else '/')+js
        try:
            s=get(ju)
            for m in sorted(set(re.findall(r'["\']([^"\']*(?:sentiment|freeserv|path=)[^"\']*)["\']',s,re.I)))[:30]: print('JSHIT',ju[-40:],m[:200])
        except Exception as e: print('JSERR',ju,e)
