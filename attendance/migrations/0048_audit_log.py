"""
Migration 0048 — AuditLog model (Phase 13)

Additive only: creates the AuditLog table. No existing table is altered.
No backfill — audit trail is forward-only from this point.

Depends on attendance 0047 (Recoverable).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('attendance', '0047_recoverable'),
    ]

    operations = [
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.BigAutoField(
                    auto_created=True, primary_key=True,
                    serialize=False, verbose_name='ID',
                )),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('actor', models.CharField(max_length=150, blank=True,
                    help_text="Username of the person who made the change, or 'system' when captured via signal.")),
                ('action', models.CharField(max_length=20, choices=[
                    ('create', 'Create'), ('update', 'Update'),
                    ('delete', 'Delete'), ('transition', 'Status Change'),
                ])),
                ('app_label',  models.CharField(max_length=50)),
                ('model_name', models.CharField(max_length=50)),
                ('object_id',  models.CharField(max_length=50, blank=True)),
                ('object_repr', models.CharField(max_length=255, blank=True)),
                ('changes', models.JSONField(default=dict, blank=True,
                    help_text="{field: [old_value, new_value], ...} — best-effort, empty for pure creates.")),
                ('note', models.CharField(max_length=255, blank=True)),
            ],
            options={
                'verbose_name': 'Audit Log Entry',
                'verbose_name_plural': 'Audit Log Entries',
                'ordering': ['-timestamp'],
            },
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['-timestamp'], name='att__audit_ts_idx'),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['app_label', 'model_name', 'object_id'], name='att__audit_obj_idx'),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['actor', '-timestamp'], name='att__audit_actor_idx'),
        ),
    ]
