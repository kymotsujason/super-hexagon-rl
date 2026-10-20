import re,glob,collections
rows=[]
for fn in sorted(glob.glob("out_*.txt"),key=lambda s:int(re.findall(r"\d+",s)[0])):
    id_=int(re.findall(r"\d+",fn)[0])
    lines=[l for l in open(fn) if l.startswith("id=")]
    ranges=collections.OrderedDict(); delays=set(); c4=set(); c8=set(); e4=set(); f5464=set()
    nw=set(); slots=set(); widths=set(); dists=set(); types=set(); shapes=set()
    for l in lines:
        m=re.search(r"rand=\[(.*?)\] delay=(\S+) c4=(\S+) c8=(\S+) e4=(\S+) 5464=(\S+) b2930=(\S+).*walls=(\d+):(.*)",l)
        r,d,a,b,e,f,base,n,w=m.groups()
        rr=tuple(x.split("/")[1] for x in r.split(",")) if r else ()
        ranges[rr]=ranges.get(rr,0)+1
        delays.add(d); c4.add(a); c8.add(b); e4.add(e); f5464.add(f); nw.add(int(n))
        ws=re.findall(r"\((-?\d+),(-?\d+),(-?\d+),(-?\d+)\)",w)
        for s,di,wi,t in ws:
            slots.add(int(s)); widths.add(int(wi)); dists.add(int(di)); types.add(int(t))
        shapes.add(tuple(sorted(ws)))
    print(f"id={id_} (0x{id_:x}) leaves={len(lines)} distinct_wallsets={len(shapes)} randseqs={dict((','.join(k),v) for k,v in ranges.items())}")
    print(f"   walls={sorted(nw)} delay={sorted(delays,key=float)} c4={sorted(c4)} c8={sorted(c8)} 53e4={sorted(e4)} 5464={sorted(f5464)}")
    print(f"   slots={sorted(slots)} widths={sorted(widths)} dist={min(dists)}..{max(dists)} types={sorted(types)}")
