import urllib.request,re
def get(u):
    return urllib.request.urlopen(urllib.request.Request(u,headers={'User-Agent':'Mozilla/5.0','Referer':'https://widgets.dukascopy.com/'}),timeout=25).read().decode('utf8','ignore')
B='https://widgets.dukascopy.com/en/'
s=get(B+'chunk-DR20n9kg.js')
for k in ['getSentimentIndexes(e','getCurrencySentimentIndexes(e','loadTopSentimentIndexes(e','#e.get(`']:
    for m in re.finditer(re.escape(k),s): print('D',s[m.start():m.start()+260])
mj=get(B+'main-ZERWAT6F.js')
for m in sorted(set(re.findall(r'.{0,100}(?:https://[a-z0-9.-]*dukascopy\.com[^"\'`]*)[^;]{0,120}',mj)))[:30]: print('M',m[:300])
