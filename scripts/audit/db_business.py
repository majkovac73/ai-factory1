import sqlite3, os, json, textwrap
DB = "/data/app.db" if os.path.exists("/data/app.db") else os.environ.get("DATABASE_PATH","app.db")
c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
def cols(t): return [d[1] for d in c.execute(f"PRAGMA table_info({t})")]
print("tasks cols:", cols("tasks"))
# recent task sample (trim long fields)
r = c.execute("select * from tasks order by created_at desc limit 1").fetchone()
if r:
    for k in r.keys():
        v = str(r[k])
        print(f"  {k}: {textwrap.shorten(v, 220)}")
print("\ntask date range:", c.execute("select min(created_at), max(created_at) from tasks").fetchone()[:])
# listings published: any listing_id-ish column?
tcols = cols("tasks")
for lc in ["listing_id","etsy_listing_id","published","status"]:
    if lc in tcols:
        print(f"tasks.{lc} nonnull:", c.execute(f"select count(*) from tasks where {lc} is not null and {lc}!=''").fetchone()[0])
print("\n=== analytics_events.event_type dist ===")
for row in c.execute("select event_type, count(*) from analytics_events group by event_type order by 2 desc"):
    print(f"  {row[0]:30s} {row[1]}")
print("\n=== any SALE/REVENUE/RECEIPT events? ===")
for row in c.execute("select event_type,entity_type,entity_id,value,substr(payload,1,180),created_at from analytics_events where lower(event_type) like '%sale%' or lower(event_type) like '%receipt%' or lower(event_type) like '%revenue%' or lower(event_type) like '%order%' or lower(event_type) like '%purchase%' order by created_at desc limit 20"):
    print(" ", tuple(row))
print("\n=== marketing_posts channel x status ===")
for row in c.execute("select channel,status,count(*) from marketing_posts group by channel,status order by 1,2"):
    print(f"  {row[0]:12s} {row[1]:12s} {row[2]}")
print("\n=== marketing_posts recent 5 ===")
for row in c.execute("select channel,status,external_url,error_message,created_at from marketing_posts order by created_at desc limit 5"):
    print(" ", tuple(str(x)[:80] for x in row))
print("\n=== image_assets by use_case / provider / model ===")
for row in c.execute("select use_case,provider,model,count(*) from image_assets group by use_case,provider,model order by 4 desc limit 20"):
    print(f"  {str(row[0]):18s} {str(row[1]):12s} {str(row[2]):28s} {row[3]}")
