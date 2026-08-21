"""The presence meter's table (0.6.0) — pure expand, no data migration.

One CREATE TABLE and nothing else: no column is dropped, no existing row is
read or rewritten, and every reader of Room/RoomParticipant behaves exactly as
it did. A deployment that never turns the provider webhooks on simply keeps an
empty table.
"""
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('video', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='ParticipantSpan',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('room_key', models.CharField(db_index=True, max_length=255)),
                ('user_id', models.CharField(db_index=True, max_length=64)),
                ('connection_id', models.CharField(db_index=True, max_length=128)),
                ('joined_at', models.DateTimeField()),
                ('left_at', models.DateTimeField(blank=True, null=True)),
                ('close_reason', models.CharField(blank=True, choices=[('explicit', 'Explicit leave'), ('webhook', 'Provider webhook'), ('sweeper', 'Reconciled by the sweeper')], default='', max_length=16)),
                ('last_seen_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'ordering': ['joined_at', 'id'],
                'indexes': [models.Index(fields=['user_id', 'joined_at'], name='video_span_user_joined'), models.Index(fields=['room_key', 'joined_at'], name='video_span_room_joined'), models.Index(fields=['left_at'], name='video_span_open')],
                'constraints': [models.UniqueConstraint(fields=('connection_id', 'joined_at'), name='video_span_uniq')],
            },
        ),
    ]
