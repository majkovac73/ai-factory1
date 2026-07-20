from config import settings
print("BACKUP_S3_BUCKET set:", bool(getattr(settings,"BACKUP_S3_BUCKET",None)))
for k in ["BACKUP_ENABLED","BACKUP_SCHEDULE_HOURS","DISCORD_WEBHOOK_URL","ALERT_WEBHOOK_URL","DEFAULT_MODEL","CONCEPT_MODEL"]:
    print(f"  {k} =", getattr(settings,k,"<MISSING>"))
