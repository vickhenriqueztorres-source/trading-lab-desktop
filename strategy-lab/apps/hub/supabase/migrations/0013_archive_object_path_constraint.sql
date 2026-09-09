-- CAT-17 / R-HUB-7: correct the over-escaped Parquet path check from migration 0012.
-- This is additive migration history: never rewrite an already-applied migration.

alter table public.cold_archive_objects
  drop constraint if exists cold_archive_objects_object_path_check;

alter table public.cold_archive_objects
  add constraint cold_archive_objects_object_path_check
  check (
    object_path ~ '^parquet/[A-Z0-9._-]+/[0-9]{10}-[0-9]{10}-[0-9a-f]{16}\.parquet$'
  );
