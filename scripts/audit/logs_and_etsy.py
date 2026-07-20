import os, json, asyncio, sqlite3
from config import settings
DB="/data/app.db"
c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
def cols(t): return [d[1] for d in c.execute(f"PRAGMA table_info({t})")]
print("logs cols:", cols("logs"))
lc=cols("logs")
lvl = "level" if "level" in lc else ("log_level" if "log_level" in lc else None)
msg = "message" if "message" in lc else ("msg" if "msg" in lc else None)
ts = "created_at" if "created_at" in lc else "timestamp"
if lvl:
    print("level dist:", {r[0]:r[1] for r in c.execute(f"select {lvl},count(*) from logs group by {lvl}")})
print("\n=== recent ERROR/CRITICAL logs ===")
for r in c.execute(f"select {ts},{lvl},{msg} from logs where {lvl} in ('ERROR','CRITICAL') order by {ts} desc limit 12"):
    print("  ",str(r[0])[:19], str(r[2])[:160])
print("\n=== trend-data / fallback / rate-limit / cost mentions ===")
for pat in ["trend","fallback","429","rate limit","backoff","seedream","cost","spend","insufficient"]:
    rows=c.execute(f"select {ts},{msg} from logs where lower({msg}) like ? order by {ts} desc limit 2",(f"%{pat}%",)).fetchall()
    if rows:
        print(f"  [{pat}]")
        for r in rows: print("     ",str(r[0])[:19], str(r[1])[:150])
print("\n=== last 8 log lines (any level) ===")
for r in c.execute(f"select {ts},{lvl},{msg} from logs order by {ts} desc limit 8"):
    print("  ",str(r[0])[:19],r[1],str(r[2])[:130])

# spend tracking state files
print("\n=== autonomy_state files (spend/task tracking) ===")
import glob
for f in sorted(glob.glob("/data/autonomy_state_*.json"))[-3:]:
    print("  ",os.path.basename(f),"=",open(f).read()[:200])

# etsy token validate
print("\n=== ETSY token validate ===")
async def e():
    import httpx
    from app.services import etsy_oauth
    tok=await etsy_oauth.get_valid_access_token()
    print("  token prefix:",(tok or "")[:12],"len",len(tok or ""))
    H={"x-api-key":settings.ETSY_API_KEY,"Authorization":f"Bearer {tok}"}
    async with httpx.AsyncClient(timeout=30) as cl:
        r=await cl.get("https://openapi.etsy.com/v3/application/users/me",headers=H)
        print("  /users/me ->",r.status_code, r.text[:160])
        # public listing read (x-api-key only)
        r2=await cl.get("https://openapi.etsy.com/v3/application/listings/4537010013",headers={"x-api-key":settings.ETSY_API_KEY})
        print("  public listing 4537010013 ->",r2.status_code, r2.text[:220])
asyncio.run(e())
