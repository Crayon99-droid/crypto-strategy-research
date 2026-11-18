import urllib.request,re
def get(u):
    return urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.dukascopy.com/'}),timeout=25).read().decode('utf8','ignore')
h=get('https://www.dukascopy.com/swiss/english/marketwatch/sentiment/')
print('len',len(h))
for m in sorted(set(re.findall(r'https?://[^"\' ]*(?:freeserv|widget|sentiment)[^"\' ]*',h)))[:40]: print('URL',m)
for m in sorted(set(re.findall(r'path=[^"\'& ]+',h)))[:40]: print('PATH',m)
for u in ['https://freeserv.dukascopy.com/2.0/?path=sentiment/index&type=swfx&jsonp=x',
          'https://freeserv.dukascopy.com/2.0/index.php?path=sentiment/get&type=swfx&jsonp=x',
          'https://www.myfxbook.com/api/get-community-outlook.json','https://www.myfxbook.com/community/outlook']:
    try: t=get(u); print('OK',u,len(t),t[:300].replace('\n',' '))
    except Exception as e: print('ERR',u,e)
