import json,statistics as st
rows=[r for r in json.load(open('rows.json')) if r['inp']]
X=[];Y=[]
for r in rows:
    A=sum(v[0] for v in r['segs'].values()); B=sum(v[1] for v in r['segs'].values())
    X.append((A,B,1.0)); Y.append(r['inp'])
# normal equations 3x3
def solve(M,v):
    n=len(v); M=[row[:]+[v[i]] for i,row in enumerate(M)]
    for i in range(n):
        p=max(range(i,n),key=lambda k:abs(M[k][i])); M[i],M[p]=M[p],M[i]
        for k in range(n):
            if k!=i:
                f=M[k][i]/M[i][i]; M[k]=[a-f*b for a,b in zip(M[k],M[i])]
    return [M[i][n]/M[i][i] for i in range(n)]
XtX=[[sum(x[i]*x[j] for x in X) for j in range(3)] for i in range(3)]
XtY=[sum(x[i]*y for x,y in zip(X,Y)) for i in range(3)]
a,b,c=solve(XtX,XtY)
res=[y-(a*x[0]+b*x[1]+c) for x,y in zip(X,Y)]
print(f"fit n={len(rows)} tok/asciichar={a:.4f} tok/cjkchar={b:.4f} intercept={c:.0f}  resid median|abs|={st.median(abs(e) for e in res):.0f} max={max(abs(e) for e in res):.0f}")
print("input_tokens median/p10/p90:",st.median(Y),sorted(Y)[len(Y)//10],sorted(Y)[9*len(Y)//10])
cr=[r['cache'] for r in rows if r['cache'] is not None]; print("first-call cache_read present",len(cr),"/",len(rows),"median",st.median(cr) if cr else None)
keys=sorted({k for r in rows for k in r['segs']})
out=[]
for k in keys:
    vals=[a*r['segs'][k][0]+b*r['segs'][k][1] for r in rows if k in r['segs']]
    out.append((st.median(vals),k,len(vals),max(vals)))
tot=0
for med,k,n,mx in sorted(out,reverse=True):
    print(f"{k:34s} present {n:3d}/{len(rows)}  median_tok {med:7.0f}  max {mx:7.0f}")
print(f"{'tool schemas (intercept)':34s} {'':16s} ~{c:7.0f}")
