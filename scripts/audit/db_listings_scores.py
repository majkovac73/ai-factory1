import sqlite3, os, json, statistics
DB = "/data/app.db" if os.path.exists("/data/app.db") else os.environ.get("DATABASE_PATH","app.db")
c = sqlite3.connect(DB); c.row_factory = sqlite3.Row

print("=== tasks.type distribution ===")
for r in c.execute("select type,count(*) from tasks group by type order by 2 desc"):
    print(f"  {str(r[0]):28s} {r[1]}")

print("\n=== listing_stats: sample payload + distinct listings ===")
rows = c.execute("select entity_id, payload, created_at from analytics_events where event_type='listing_stats' order by created_at desc limit 3").fetchall()
for r in rows: print("  ", r["entity_id"], str(r["payload"])[:200], r["created_at"])
ids = c.execute("select distinct entity_id from analytics_events where event_type='listing_stats'").fetchall()
print("  distinct listings tracked:", len(ids))
# sum views/favorites from latest payload per listing
views=faves=0; parsed=0
latest={}
for r in c.execute("select entity_id,payload,created_at from analytics_events where event_type='listing_stats' order by created_at asc"):
    latest[r["entity_id"]]=r["payload"]
for eid,p in latest.items():
    try:
        d=json.loads(p); views+=int(d.get("views",0) or 0); faves+=int(d.get("num_favorers",d.get("favorites",0)) or 0); parsed+=1
    except: pass
print(f"  parsed {parsed} listings; TOTAL views={views} favorites={faves}")

print("\n=== concept_scored: score distribution (the shadow gate) ===")
scores=[]; passes=0; details=[]
for r in c.execute("select value,payload,created_at from analytics_events where event_type='concept_scored' order by created_at desc"):
    try:
        d=json.loads(r["payload"]) if r["payload"] else {}
    except: d={}
    s = r["value"]
    if s is None: s = d.get("score")
    try: s=float(s)
    except: s=None
    if s is not None: scores.append(s)
    if len(details)<6: details.append((s, {k:d.get(k) for k in ("passed","judge","determinism","product_name","decision","enforced")}))
if scores:
    scores_sorted=sorted(scores)
    print(f"  n={len(scores)} min={min(scores)} max={max(scores)} mean={round(statistics.mean(scores),1)} median={statistics.median(scores)}")
    for thr in (90,85,80,75,70):
        print(f"    >= {thr}: {sum(1 for x in scores if x>=thr)}")
print("  sample details:")
for s,d in details: print("   ", s, d)

print("\n=== published Etsy listing IDs + prices (from tasks.output_data/result) ===")
n=0
for r in c.execute("select id,type,output_data,result,metadata from tasks order by created_at desc"):
    for fld in ("output_data","result"):
        try: d=json.loads(r[fld]) if r[fld] else {}
        except: d={}
        lid=d.get("listing_id") or d.get("etsy_listing_id"); price=d.get("price") or d.get("price_usd")
        if lid:
            print(f"  {r['type']:22s} listing={lid} price={price}")
            n+=1; break
    if n>=25: break
print("  (showing up to 25)")
