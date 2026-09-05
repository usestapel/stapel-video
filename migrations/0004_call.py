"""``Call`` (0.11.0) — pure expand: one new table, nothing touched.

No column is added to an existing table, no row is read or rewritten, and
every 0.10.0 reader keeps working. A deployment that never places a call
carries an empty table.

Two of the three constraints deserve a note, because reading them as the
gate they are not is how the next person removes the real one:

- ``video_call_one_live_as_caller`` / ``..._as_callee`` are the BACKSTOP for
  "one live call per user", not the enforcement. They cannot express the
  cross-role case — A being the callee of one call and the caller of another
  violates neither — so the gate is the query in
  ``stapel_video.calls.services._refuse_if_busy``, re-run on accept. These
  close the same-role race, which is the one a retrying client produces.
- ``video_call_two_parties`` is the database saying a call needs two people.
  The serializer says it too; this is here because the serializer is a seam a
  host may swap.

Partial unique constraints need a database that supports them (PostgreSQL,
SQLite). MySQL does not, and this module has never claimed it.
"""

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('video', '0003_participantspan_scope_key'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Call',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('thread_key', models.CharField(blank=True, db_index=True, default='', max_length=255)),
                ('room_name', models.CharField(max_length=255, unique=True)),
                ('scope_key', models.CharField(blank=True, db_index=True, default='', max_length=255)),
                ('media', models.CharField(choices=[('audio', 'Audio'), ('video', 'Video')], default='video', max_length=8)),
                ('state', models.CharField(choices=[('ringing', 'Ringing'), ('accepted', 'Accepted'), ('declined', 'Declined'), ('missed', 'Missed'), ('ended', 'Ended'), ('failed', 'Failed')], default='ringing', max_length=16)),
                ('end_reason', models.CharField(blank=True, choices=[('hangup', 'Hung up'), ('declined', 'Declined'), ('ring_timeout', 'Ring timed out'), ('remote_left', 'The other party left'), ('room_finished', 'The media room finished'), ('reconciled', 'Reconciled by the sweeper'), ('max_duration', 'Maximum duration reached'), ('provider_error', 'The video backend refused')], default='', max_length=16)),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('answered_at', models.DateTimeField(blank=True, null=True)),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('callee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='video_calls_received', to=settings.AUTH_USER_MODEL)),
                ('caller', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='video_calls_made', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-started_at'],
                'indexes': [models.Index(fields=['caller', 'state'], name='video_call_caller_state'), models.Index(fields=['callee', 'state'], name='video_call_callee_state'), models.Index(fields=['state', 'started_at'], name='video_call_state_started'), models.Index(fields=['thread_key', 'started_at'], name='video_call_thread')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('state__in', ('ringing', 'accepted'))), fields=('caller',), name='video_call_one_live_as_caller'), models.UniqueConstraint(condition=models.Q(('state__in', ('ringing', 'accepted'))), fields=('callee',), name='video_call_one_live_as_callee'), models.CheckConstraint(condition=models.Q(('caller', models.F('callee')), _negated=True), name='video_call_two_parties')],
            },
        ),
    ]
