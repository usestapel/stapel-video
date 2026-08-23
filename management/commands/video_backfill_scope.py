"""Stamp ``scope_key`` onto the spans recorded before the grant carried one.

    python manage.py video_backfill_scope --resolver myapp.reporting.scope_for_room
                                          [--batch-size N] [--limit N] [--dry-run]

The resolver is the HOST's, and it has to be: a span holds an opaque
``room_key``, and only the host knows which partition a room belonged to. For
a workspace product it is one query::

    def scope_for_room(room_key):
        return Room.objects.filter(code=room_key).values_list(
            "workspace_id", flat=True).first()

Idempotent and resumable by construction — it only ever touches rows where
``scope_key IS NULL``, so a re-run after a crash picks up exactly what the
crash left, and a second full run is a no-op. Rooms are resolved once each
and the spans are updated in batches, because the population this exists for
is "every span since the meter shipped".

A resolver that answers ``None`` for a room is not an error: some rooms
genuinely belong to no partition (a support call, a demo), and those spans
stay NULL rather than being forced into a tenant. They are counted and
reported so the operator can tell "nothing to resolve" from "the resolver is
looking in the wrong table".
"""
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = (
        "Backfill ParticipantSpan.scope_key from a host callable "
        "room_key -> scope_key|None, over spans that have no scope yet."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--resolver",
            required=True,
            help=(
                "Dotted path to a callable taking a room_key and returning the "
                "scope_key it belongs to, or None."
            ),
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Spans updated per UPDATE statement (default 1000).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help=(
                "Stop after this many distinct rooms. For running the "
                "backfill in bounded slices on a large table."
            ),
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Resolve and report, write nothing.",
        )

    def handle(self, *args, **options):
        from django.utils.module_loading import import_string

        from ...presence import backfill_scope_keys

        try:
            resolver = import_string(options["resolver"])
        except ImportError as exc:
            raise CommandError(
                f"--resolver {options['resolver']!r} could not be imported: {exc}"
            ) from exc
        if not callable(resolver):
            raise CommandError(f"--resolver {options['resolver']!r} is not callable")

        result = backfill_scope_keys(
            resolver,
            batch_size=options["batch_size"],
            limit=options["limit"],
            dry_run=options["dry_run"],
        )
        verb = "would stamp" if options["dry_run"] else "stamped"
        self.stdout.write(
            f"video_backfill_scope: {result['rooms']} unscoped room(s), "
            f"{result['resolved']} resolved, {result['unresolved']} left "
            f"unscoped by the resolver — {verb} {result['spans']} span(s)."
        )
        if result["unresolved"] and not result["resolved"]:
            self.stdout.write(
                self.style.WARNING(
                    "The resolver answered None for every room. That is a "
                    "legitimate answer, but it is also what a resolver "
                    "pointed at the wrong table looks like — check one "
                    "room_key by hand before assuming the meter is unscoped."
                )
            )
