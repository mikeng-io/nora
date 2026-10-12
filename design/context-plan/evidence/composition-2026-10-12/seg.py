import json,re,glob,statistics as st
CJK=re.compile(r'[\u3000-\u9fff\uff00-\uffef]')
def ac(s):
    k=len(CJK.findall(s)); return (len(s)-k,k)
rows=[]
for f in glob.glob('t_*.json'):
    t=json.load(open(f))
    gens=sorted([o for o in t.get('observations',[]) if o['type']=='GENERATION'],key=lambda o:o['startTime'])
    if not gens: continue
    g=gens[0]; inp=g.get('input')
    if not isinstance(inp,list) or not inp or inp[0].get('role')!='system': continue
    u=g.get('usageDetails') or {}
    segs={}
    sysc=inp[0]['content']
    for m in re.finditer(r'<section[^>]*name=\\?"([a-z_\-]+)\\?"[^>]*>.*?</section>',sysc,re.S):
        segs['doctrine:'+m.group(1)]=ac(m.group(0))
    for tag in ['available_skills','runtime_context']:
        m=re.search(r'<%s[^>]*>.*?</%s>'%(tag,tag),sysc,re.S)
        if m: segs['system:'+tag]=ac(m.group(0))
    covered=sum(a+b for a,b in segs.values())
    segs['system:other']=(len(sysc)-covered,0)
    for i,msg in enumerate(inp[1:],1):
        c=msg['content'] if isinstance(msg['content'],str) else json.dumps(msg['content'],ensure_ascii=False)
        cov=0
        # top-level tags only: scan sequentially
        pos=0
        while True:
            m=re.compile(r'<([a-z_][a-z0-9_]*)(?:\s[^>]*)?>').search(c,pos)
            if not m: break
            tag=m.group(1); e=c.find('</%s>'%tag,m.end())
            if e<0: pos=m.end(); continue
            e+=len(tag)+3; key='block:'+tag
            a,b=ac(c[m.start():e]); pa,pb=segs.get(key,(0,0)); segs[key]=(pa+a,pb+b); cov+=e-m.start(); pos=e
        a,b=ac(c); 
        if len(c)-cov>0: pa,pb=segs.get('msg:untagged',(0,0)); segs['msg:untagged']=(pa+max(0,a-cov),pb)
    rows.append(dict(ts=g['startTime'],inp=u.get('input'),cache=u.get('cache_read_input_tokens'),out=u.get('output'),segs=segs,ngen=len(gens)))
json.dump(rows,open('rows.json','w'))
print("turns",len(rows),"window",min(r['ts'] for r in rows),max(r['ts'] for r in rows))
