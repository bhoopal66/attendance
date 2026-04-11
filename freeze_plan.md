# Payroll Freeze Feature Plan

## Problem

Payroll is computed live from current DB values. If an employee's salary, payroll type (fixed vs attendance-based), or a bank's commission rate changes — it silently alters all previously displayed payroll figures. We need to "freeze" a completed month so its values are permanently locked.

---

## Approach: Snapshot-on-Freeze

When admin freezes a month:
1. Run existing computation for every employee once
2. Save each row as a JSON snapshot in the DB
3. On future loads of that month, read snapshots instead of recomputing
4. Show "FROZEN" badge; disable edit controls for that month

---

## New Models (`payroll/models.py`)

### `PayrollFreeze`
One per month — tracks freeze status.

```python
class PayrollFreeze(models.Model):
    year = models.IntegerField()
    month = models.IntegerField()
    is_frozen = models.BooleanField(default=False)
    frozen_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        unique_together = [('year', 'month')]
```

### `FrozenPayrollRecord`
One per employee per frozen month — stores the full computed row.

```python
class FrozenPayrollRecord(models.Model):
    employee = models.ForeignKey('attendance.Employee', null=True, blank=True, on_delete=models.SET_NULL)
    remote_employee = models.ForeignKey('attendance.RemoteEmployee', null=True, blank=True, on_delete=models.SET_NULL)
    year = models.IntegerField()
    month = models.IntegerField()
    snapshot = models.JSONField()  # full computed row dict
    net_payroll = models.DecimalField(max_digits=12, decimal_places=2)
    frozen_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [
            ('employee', 'year', 'month'),
            ('remote_employee', 'year', 'month'),
        ]
```

**Why JSONField:** The row dicts from `_get_inhouse_payroll_row()` and `_get_sales_payroll_row()` are already structured dicts with all breakdown fields. Storing as JSON avoids maintaining a separate schema per calculation path. `net_payroll` is stored separately as Decimal for filtering/aggregation.

---

## New Migration

Run `python manage.py makemigrations && python manage.py migrate` after adding the models.

---

## New API Views (`payroll/views.py`)

### `freeze_payroll(request)` — POST `/payroll/api/freeze/`

1. Get or create `PayrollFreeze(year, month)`, check not already frozen
2. Pull same querysets as `payroll_view` (employees, banks, holidays, carryovers)
3. For each employee call existing `_get_inhouse_payroll_row()` or `_get_sales_payroll_row()` — **no new computation logic**
4. Upsert `FrozenPayrollRecord` with the row dict as `snapshot`
5. Set `PayrollFreeze.is_frozen = True`, `frozen_at = now()`
6. Return `{"success": true, "frozen_at": "..."}`

### `unfreeze_payroll(request)` — POST `/payroll/api/unfreeze/`

1. Find `PayrollFreeze(year, month)`, set `is_frozen = False`
2. Delete all `FrozenPayrollRecord` for that month (fresh data on next freeze)
3. Return `{"success": true}`

---

## Modify `payroll_view()` (`payroll/views.py`)

At the top of the view, after resolving `year`/`month`:

```python
freeze_obj = PayrollFreeze.objects.filter(year=year, month=month, is_frozen=True).first()
is_frozen = freeze_obj is not None
```

- **If frozen:** load `FrozenPayrollRecord` objects, reconstruct rows from `.snapshot` dicts. Skip all `_get_inhouse_payroll_row()` / `_get_sales_payroll_row()` calls.
- **If not frozen:** current behavior unchanged.

Pass `is_frozen` and `freeze_obj` to the template context.

---

## New URLs (`payroll/urls.py`)

```python
path('api/freeze/', views.freeze_payroll, name='freeze_payroll'),
path('api/unfreeze/', views.unfreeze_payroll, name='unfreeze_payroll'),
```

---

## Template Changes

1. **Freeze button** in page header (shown when `not is_frozen`):
   - JS confirm: *"Freeze [Month Year]? This will lock all payroll values as-is."*

2. **Frozen badge** (shown when `is_frozen`):
   - Display: `FROZEN — [date]` + Unlock button

3. **Disable edit controls** when frozen:
   - Wrap adjustment add/delete buttons and submission edit fields with `{% if not is_frozen %}` guards

---

## Files to Modify

| File | Change |
|------|--------|
| `payroll/models.py` | Add `PayrollFreeze`, `FrozenPayrollRecord` |
| `payroll/views.py` | Add `freeze_payroll()`, `unfreeze_payroll()`, modify `payroll_view()` |
| `payroll/urls.py` | Add 2 new URL patterns |
| Payroll template | Freeze button, frozen badge, disable edits when frozen |

---

## Verification Steps

1. `python manage.py makemigrations && python manage.py migrate`
2. Open payroll for current month → loads normally (unfrozen)
3. Click "Freeze Payroll" → frozen badge appears, snapshot records exist in DB
4. Change an employee's salary → reload frozen month → figures unchanged ✓
5. Change a bank's commission rate → reload frozen month → commission unchanged ✓
6. Click "Unlock" → change data → re-freeze → snapshot updates ✓
7. Verify edit buttons hidden on frozen months ✓
