# Plan: Deduction Carryover System

## Context

Currently, the payroll dashboard allows final salaries to go negative when deductions exceed earnings. There is no protection or carryover logic — negative values are simply displayed in red. The fix: when deductions would push a salary below zero, cap the salary at zero and carry the undeducted overflow into the next month automatically. A new sub-tab inside the Deductions section will show the full month-by-month overflow history per employee.

---

## 1. New Model — `DeductionCarryover` (`payroll/models.py`)

Add after `DeductionEntry`:

```python
class DeductionCarryover(models.Model):
    employee = models.ForeignKey('attendance.Employee', on_delete=models.CASCADE, null=True, blank=True)
    remote_employee = models.ForeignKey('attendance.RemoteEmployee', on_delete=models.CASCADE, null=True, blank=True)
    from_year = models.IntegerField()
    from_month = models.IntegerField()       # month where salary went negative
    to_year = models.IntegerField()
    to_month = models.IntegerField()         # = from + 1 month
    overflow_amount = models.DecimalField(max_digits=10, decimal_places=2)  # what couldn't be deducted
    applied_amount = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('0'))  # how much was finally applied in to_month
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['employee', 'from_year', 'from_month'],
                                    condition=models.Q(employee__isnull=False),
                                    name='unique_inhouse_carryover_month'),
            models.UniqueConstraint(fields=['remote_employee', 'from_year', 'from_month'],
                                    condition=models.Q(remote_employee__isnull=False),
                                    name='unique_remote_carryover_month'),
        ]
```

---

## 2. Migration

Create `payroll/migrations/0012_deductioncarryover.py`.

---

## 3. Dashboard View Changes (`payroll/views.py`)

### 3a. Load incoming carryovers for selected month (around line 468)

Before building `section3_rows`, query:
```python
incoming_carryovers = DeductionCarryover.objects.filter(
    to_year=selected_year, to_month=selected_month
)
# Build dict: ('inhouse'|'remote', emp_id) → overflow_amount
carryover_by_emp = {}
for co in incoming_carryovers:
    key = ('inhouse', co.employee_id) if co.employee_id else ('remote', co.remote_employee_id)
    carryover_by_emp[key] = co.overflow_amount
```

### 3b. Include carryover in each employee's total deductions (around line 516)

After computing `total_ded`, add:
```python
carryover_in = float(carryover_by_emp.get(emp_key, 0))
total_ded = round(total_ded + carryover_in, 2)
row['carryover_in'] = carryover_in
```

### 3c. Detect overflow and persist carryover (after final salary computation in Section 5, around line 567)

After computing `final_salary = payroll_net - total_ded + total_add`:

```python
if final_salary < 0:
    overflow = abs(final_salary)
    final_salary = Decimal('0')
    # Compute to_month (next calendar month)
    to_idx = selected_year * 12 + selected_month
    to_year, to_month = divmod(to_idx, 12)
    if to_month == 0: to_month = 12; to_year -= 1
    # Persist — upsert so re-rendering is safe
    DeductionCarryover.objects.update_or_create(
        **fk_kwargs,
        from_year=selected_year, from_month=selected_month,
        defaults={'overflow_amount': overflow, 'to_year': to_year, 'to_month': to_month}
    )
else:
    # No overflow — clear any stale carryover record for this month
    DeductionCarryover.objects.filter(**fk_kwargs, from_year=selected_year, from_month=selected_month).delete()
```

Also update `applied_amount` on the incoming carryover record (mark how much was consumed):
```python
if carryover_in > 0:
    incoming_co = incoming_carryovers.filter(**fk_kwargs).first()
    if incoming_co:
        incoming_co.applied_amount = Decimal(str(min(carryover_in, float(incoming_co.overflow_amount))))
        incoming_co.save(update_fields=['applied_amount'])
```

### 3d. Pass carryover history to context

```python
all_carryovers = DeductionCarryover.objects.filter(
    models.Q(employee__in=active_inhouse) | models.Q(remote_employee__in=active_remote)
).select_related('employee', 'remote_employee').order_by('-from_year', '-from_month')
context['all_carryovers'] = all_carryovers
```

---

## 4. Template Changes (`payroll/templates/payroll/dashboard.html`)

### 4a. Convert Deductions tab to have two sub-tabs

Inside `#payrollPanel3`, add a sub-tab nav above the existing content:

```html
<!-- Sub-tabs -->
<div class="ded-subtab-nav">
    <button class="ded-subtab active" onclick="showDedSubtab('summary', this)">Monthly Summary</button>
    <button class="ded-subtab" onclick="showDedSubtab('carryover', this)">Carryover Schedule</button>
</div>

<!-- Sub-tab 1: existing deductions table + entries list (unchanged) -->
<div id="dedSubtab-summary"> ... existing content ... </div>

<!-- Sub-tab 2: Carryover Schedule -->
<div id="dedSubtab-carryover" style="display:none;">
    <table class="payroll-table">
        <thead>
            <tr>
                <th>Employee</th>
                <th>Overflow Month</th>
                <th class="text-right">Overflow Amount</th>
                <th>Carries To</th>
                <th class="text-right">Applied</th>
                <th>Status</th>
            </tr>
        </thead>
        <tbody>
            {% for co in all_carryovers %}
            <tr>
                <td>{{ co.employee.name|default:co.remote_employee.name }}</td>
                <td>{{ co.from_month }}/{{ co.from_year }}</td>
                <td class="text-right text-red mono">{{ co.overflow_amount|floatformat:2 }}</td>
                <td>{{ co.to_month }}/{{ co.to_year }}</td>
                <td class="text-right mono">
                    {% if co.applied_amount > 0 %}{{ co.applied_amount|floatformat:2 }}{% else %}—{% endif %}
                </td>
                <td>
                    {% if co.applied_amount >= co.overflow_amount %}
                        <span class="adj-badge incentive">Applied</span>
                    {% elif co.applied_amount > 0 %}
                        <span class="adj-badge">Partial</span>
                    {% else %}
                        <span class="adj-badge reduction">Pending</span>
                    {% endif %}
                </td>
            </tr>
            {% empty %}
            <tr>
                <td colspan="6" style="text-align:center;color:#94a3b8;padding:24px;">No carryovers recorded.</td>
            </tr>
            {% endfor %}
        </tbody>
    </table>
</div>
```

### 4b. Add sub-tab JS (small, inline)

```javascript
function showDedSubtab(name, btn) {
    document.querySelectorAll('.ded-subtab').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('dedSubtab-summary').style.display = name === 'summary' ? '' : 'none';
    document.getElementById('dedSubtab-carryover').style.display = name === 'carryover' ? '' : 'none';
}
```

### 4c. Carryover badge in Monthly Summary table

In the section3_rows table, when `row.carryover_in > 0`, show an amber badge next to the employee name indicating carried-over amount from previous month.

---

## 5. Critical Files

| File | Change |
|------|--------|
| `payroll/models.py` | Add `DeductionCarryover` model |
| `payroll/migrations/0012_deductioncarryover.py` | New migration |
| `payroll/views.py` | Load carryovers, include in totals, persist overflow (~lines 468–600) |
| `payroll/templates/payroll/dashboard.html` | Sub-tabs, carryover table, carryover badge in summary |

---

## 6. Behaviour Summary

| Scenario | Result |
|----------|--------|
| Final salary ≥ 0 | No change. Any stale carryover for this month is cleared. |
| Final salary < 0 | Clamped to 0. Overflow saved as `DeductionCarryover` pointing to next month. |
| Viewing a month with incoming carryover | Overflow added to that employee's deductions automatically. |
| Chain (M+1 also negative) | Another `DeductionCarryover` created pointing to M+2, and so on. |
| Re-rendering same month | `update_or_create` prevents duplicate carryover records. |

---

## 7. Verification

1. Set an employee's salary lower than their deductions for a month
2. Load the payroll dashboard for that month — final salary should show 0, not negative
3. Navigate to next month — deduction total should include the carried overflow
4. Open "Carryover Schedule" sub-tab — entry should appear with correct amounts and status
5. If next month's salary also goes negative, verify the chain continues to the following month
