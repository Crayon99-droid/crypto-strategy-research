import urllib.request,re
def get(u):
    return urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.dukascopy.com/'}),timeout=25).read().decode('utf8','ignore')
t=get('https://widgets.dukascopy.com/en/')
base=re.findall(r'<base href="([^"]+)"',t); print('BASE',base)
for js in re.findall(r'src=["\']([^"\']+\.js)',t):
    for pre in ['https://widgets.dukascopy.com/en/','https://widgets.dukascopy.com/']:
        try:
            s=get(pre+js); print('GOT',pre+js,len(s))
            for m in sorted(set(re.findall(r'.{0,120}(?:sentiment|freeserv)[^;]{0,160}',s,re.I)))[:25]: print('JSHIT',m[:300])
            break
        except Exception as e: pass
