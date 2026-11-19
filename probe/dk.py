import urllib.request,re
def get(u):
    return urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.dukascopy.com/'}),timeout=25).read().decode('utf8','ignore')
B='https://widgets.dukascopy.com/en/'
s=get(B+'chunk-DR20n9kg.js'); print('LEN',len(s))
for m in sorted(set(re.findall(r'.{0,150}(?:http|freeserv|path|api|\.json|fetch|get\()[^;]{0,200}',s)))[:40]: print('C',m[:350])
for imp in sorted(set(re.findall(r"chunk-[A-Za-z0-9_-]+\.js",s)))[:12]:
    try:
        t=get(B+imp)
        for m in sorted(set(re.findall(r'.{0,150}(?:freeserv|sentiment)[^;]{0,200}',t,re.I)))[:10]: print('I',imp,m[:350])
    except Exception as e: print('IE',imp,e)
