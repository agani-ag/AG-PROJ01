# Database Reset & Master Data Quick Reference

## 🚀 Quick Commands

### Load Master Data (First Time)
```bash
python manage.py loadmasterdata
```

### Reset and Reload All Master Data
```bash
python manage.py loadmasterdata --reset
```

## 🔄 Complete Database Reset Workflow

### For SQLite (Development)
```bash
# 1. Delete database
del maincore.sqlite3  # Windows
# rm maincore.sqlite3  # Linux/Mac

# 2. Run migrations
python manage.py migrate

# 3. Load master data
python manage.py loadmasterdata

# 4. Create admin user (optional)
python manage.py createadminuser
```

### For Production/PostgreSQL
```bash
# 1. Drop and recreate database (use psql or pgAdmin)

# 2. Run migrations
python manage.py migrate

# 3. Load master data
python manage.py loadmasterdata

# 4. Create admin user
python manage.py createadminuser
```

## ✅ What Gets Loaded?

| Table | Records | Purpose |
|-------|---------|---------|
| **ConstructionStage** | 15 | Site Prep → Foundation → ... → Handover |
| **LeadStatus** | 10 | New → Contacted → Won/Lost |
| **WorkerRole** | 15 | Mason, Carpenter, Plumber, etc. |
| **RequirementStatus** | 5 | Open → Assigned → Completed |
| **UrgencyLevel** | 5 | Low → Medium → High → Urgent → Critical |
| **CreditTransactionType** | 7 | Credit Issue, Payment, Write-off, etc. |

**Total:** 67 master records

## 🛡️ Safety Features

- ✅ **Idempotent:** Safe to run multiple times (won't duplicate)
- ✅ **get_or_create:** Only creates if `code` doesn't exist
- ✅ **Transaction Safe:** All-or-nothing (atomic)
- ⚠️ **Reset Flag:** Use `--reset` carefully (deletes existing data)

## 📋 Verification Commands

### Check record counts
```bash
python manage.py shell
```

```python
from objectbank.models import *

# Verify all master tables
print(f"Construction Stages: {ConstructionStage.objects.count()}")  # 15
print(f"Lead Statuses: {LeadStatus.objects.count()}")              # 10
print(f"Worker Roles: {WorkerRole.objects.count()}")               # 15
print(f"Requirement Statuses: {RequirementStatus.objects.count()}")# 5
print(f"Urgency Levels: {UrgencyLevel.objects.count()}")           # 5
print(f"Credit Txn Types: {CreditTransactionType.objects.count()}") # 7

# List all construction stages
for stage in ConstructionStage.objects.all():
    print(f"{stage.sequence_order}. {stage.name}")
```

## 🔧 Customization

### To add new master data:

1. Edit: [objectbank/management/commands/loadmasterdata.py](../objectbank/management/commands/loadmasterdata.py)
2. Add entry to appropriate list
3. Run: `python manage.py loadmasterdata`

**Example:** Add new worker role
```python
{'code': 'AC_TECH', 'name': 'AC Technician', 'is_active': True}
```

## 📚 Documentation

- [Full Master Data Reference](MASTER_DATA.md) - Complete data dictionary
- [Database Schema](DATABASE_SCHEMA.md) - Entity relationships
- [Architecture](ARCHITECTURE.md) - System overview

## ⚠️ Important Notes

1. **Do NOT delete master records** if they have foreign key references
2. **Use `is_active=False`** instead of deleting (where available)
3. **Backup before `--reset`** in production
4. Master data `code` fields are immutable (don't change after creation)

## 🎯 Post-Load Next Steps

After loading master data, you can:

1. **Create test data** (projects, workers)
2. **Set up admin user** (`createadminuser`)
3. **Access admin panel** at `http://localhost:8000/admin/`
4. **Start using the application**

---

**Commands Available:**
- `python manage.py loadmasterdata` - Load master tables
- `python manage.py createadminuser` - Create admin user
- `python manage.py migrate` - Apply database migrations
- `python manage.py runserver` - Start development server
