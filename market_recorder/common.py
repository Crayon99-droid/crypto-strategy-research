import json, time, urllib.request, urllib.parse
UA={"User-Agent":"helios-recorder/1.0"}
def get(url, params=None, tries=4, body=None):
    if params: url += "?" + urllib.parse.urlencode(params)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={**UA, **({"Content-Type":"application/json"} if body else {})},
                                         data=json.dumps(body).encode() if body else None)
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read())
        except Exception as e:
            if i == tries-1 or any(c in str(e) for c in ("451","403 Forbidden","Error 403")): raise   # geo-block: don't waste time
            time.sleep(2*(i+1) + (30 if "429" in str(e) or "418" in str(e) else 0))
def f(x):
    try: return float(x)
    except (TypeError, ValueError): return None
