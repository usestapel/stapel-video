"""``ParticipantSpan.scope_key`` (0.7.0) — pure expand, no data migration.

One nullable column and one index. Nothing is dropped, no existing row is
read or rewritten, and every 0.6.0 reader keeps working: a span written
before this release simply has no scope, which is the same state a host that
partitions nothing writes today.

Filling the column for history is deliberately NOT here. Only the host knows
which partition a `room_key` belonged to, so the backfill is a command it
runs with its own resolver (``manage.py video_backfill_scope --resolver``),
not a RunPython this library could ever write.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('video', '0002_participantspan'),
    ]

    operations = [
        migrations.AddField(
            model_name='participantspan',
            name='scope_key',
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
        migrations.AddIndex(
            model_name='participantspan',
            index=models.Index(fields=['scope_key', 'joined_at'], name='video_span_scope_joined'),
        ),
    ]
