# Auto-Generated Codes - Quick Reference

## ✅ Implemented

**Project Code** and **Worker Code** are now **automatically generated** when creating new records.

---

## 📋 Code Formats

### Project Code Format
```
PROJ-YYYY-####
```

**Examples:**
- `PROJ-2026-0001` - First project of 2026
- `PROJ-2026-0002` - Second project of 2026
- `PROJ-2027-0001` - First project of 2027 (resets each year)

### Worker Code Format
```
WKR-YYYY-####
```

**Examples:**
- `WKR-2026-0001` - First worker of 2026
- `WKR-2026-0002` - Second worker of 2026
- `WKR-2027-0001` - First worker of 2027 (resets each year)

---

## 🔧 How It Works

### Automatic Generation
1. User fills out the form **WITHOUT** entering code
2. On save, system auto-generates code:
   - Uses current year (YYYY)
   - Finds last sequence number for that year
   - Increments by 1
   - Creates unique code

### Code Structure
- **Prefix:** PROJ or WKR (identifies entity type)
- **Year:** 4-digit current year
- **Sequence:** 4-digit zero-padded number (0001-9999)

### Yearly Reset
- Sequence numbers restart at 0001 each January 1st
- Format: `PROJ-2026-0357` → `PROJ-2027-0001` (new year)

---

## 🎯 User Experience

### Creating New Project
**BEFORE (Manual Entry):**
```
Project Code: [User types PROJ-2026-0045]  ❌ Error-prone
Project Name: [User enters name]
```

**AFTER (Auto-Generated):**
```
Project Name: [User enters name]           ✅ Code auto-generated
(No code field shown - system handles it)
```

### Creating New Worker
**BEFORE (Manual Entry):**
```
Worker Code: [User types WKR-2026-0123]    ❌ Error-prone
Worker Name: [User enters name]
```

**AFTER (Auto-Generated):**
```
Worker Name: [User enters name]            ✅ Code auto-generated
(No code field shown - system handles it)
```

---

## 🛡️ Benefits

### For Users:
✅ **No manual typing** - Eliminates human error  
✅ **Faster data entry** - One less field to fill  
✅ **No duplicate codes** - System enforces uniqueness  
✅ **No number tracking** - System remembers last sequence

### For System:
✅ **Guaranteed uniqueness** - Database constraint + auto-increment  
✅ **Consistent format** - All codes follow same pattern  
✅ **Easy traceability** - Year embedded in code  
✅ **Sortable** - Codes sort chronologically

---

## 📊 Technical Details

### Database Changes
```python
# Project Model
project_code = models.CharField(max_length=20, unique=True, blank=True)
# blank=True allows empty string during form submission

# Worker Model
worker_code = models.CharField(max_length=20, unique=True, blank=True)
```

### Generation Logic (in models.py)
```python
def save(self, *args, **kwargs):
    if not self.project_code:
        year = date.today().year
        last_project = Project.objects.filter(
            project_code__startswith=f"PROJ-{year}-"
        ).order_by('project_code').last()
        
        if last_project:
            last_seq = int(last_project.project_code.split('-')[-1])
            new_seq = last_seq + 1
        else:
            new_seq = 1
        
        self.project_code = f"PROJ-{year}-{new_seq:04d}"
    
    super().save(*args, **kwargs)
```

---

## 🔄 Migration Applied

**Migration:** `0003_alter_project_project_code_alter_worker_worker_code`

**Changes:**
- `project_code` field: Added `blank=True`
- `worker_code` field: Added `blank=True`

**Status:** ✅ Applied successfully

---

## 🧪 Testing

### Test Creating Project:
1. Go to: `http://localhost:8000/projects/create/`
2. Fill only: Name, Client, Phone, Address, etc.
3. **Do NOT enter Project Code**
4. Click "Save Project"
5. **Result:** Code auto-generated as `PROJ-2026-0001`

### Test Creating Worker:
1. Go to: `http://localhost:8000/workers/create/`
2. Fill only: Name, Role, Phone, Pincode, Date
3. **Do NOT enter Worker Code**
4. Click "Save Worker"
5. **Result:** Code auto-generated as `WKR-2026-0001`

---

## 📌 Important Notes

### Uniqueness Guarantee
- Database has `unique=True` constraint
- If code collision occurs (rare), Django raises `IntegrityError`
- System will NOT create duplicate codes

### Manual Override (Advanced)
- Users can still manually set codes via Django Admin or API
- If manual code provided, auto-generation is skipped
- Useful for data migration from legacy systems

### Code Limit
- Max 9,999 projects per year (`PROJ-YYYY-9999`)
- Max 9,999 workers per year (`WKR-YYYY-9999`)
- If limit reached, increase format: `%05d` for 99,999

---

## 🎨 UI Changes

### Forms Updated:
- ✅ `project_create.html` - Removed project_code field
- ✅ `worker_create.html` - Removed worker_code field

### Forms Configuration:
- ✅ `ProjectForm` - Excluded `project_code` from fields
- ✅ `WorkerForm` - Excluded `worker_code` from fields

---

## ✅ Verification Commands

### Check auto-generated codes:
```bash
python manage.py shell
```

```python
from objectbank.models import Project, Worker

# Create test project
p = Project.objects.create(
    name="Test Project",
    client_name="Test Client",
    phone="1234567890",
    full_address="Test Address",
    pincode="560001",
    city="Bangalore",
    current_stage_id=1,
    lead_status_id=1
)
print(p.project_code)  # Output: PROJ-2026-0001

# Create test worker
w = Worker.objects.create(
    name="Test Worker",
    role_id=1,
    phone="9876543210",
    primary_pincode="560001",
    joined_date="2026-02-12"
)
print(w.worker_code)  # Output: WKR-2026-0001
```

---

**Status:** ✅ FULLY IMPLEMENTED  
**Last Updated:** February 12, 2026
