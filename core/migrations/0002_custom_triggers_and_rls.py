from django.db import migrations


def apply_custom_sql(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            CREATE SEQUENCE IF NOT EXISTS video_seq_seq;

            CREATE OR REPLACE FUNCTION set_updated_at() RETURNS trigger AS $$
            BEGIN
                NEW.updated_at = now();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;

            DROP TRIGGER IF EXISTS trg_videos_updated ON videos;
            CREATE TRIGGER trg_videos_updated
                BEFORE UPDATE ON videos
                FOR EACH ROW
                EXECUTE FUNCTION set_updated_at();

            DROP TRIGGER IF EXISTS trg_settings_updated ON settings;
            CREATE TRIGGER trg_settings_updated
                BEFORE UPDATE ON settings
                FOR EACH ROW
                EXECUTE FUNCTION set_updated_at();

            DROP TRIGGER IF EXISTS trg_accounts_updated ON accounts;
            CREATE TRIGGER trg_accounts_updated
                BEFORE UPDATE ON accounts
                FOR EACH ROW
                EXECUTE FUNCTION set_updated_at();

            ALTER TABLE videos ENABLE ROW LEVEL SECURITY;
            ALTER TABLE settings ENABLE ROW LEVEL SECURITY;
            ALTER TABLE accounts ENABLE ROW LEVEL SECURITY;
            ALTER TABLE activity_log ENABLE ROW LEVEL SECURITY;

            DO $$
            BEGIN
                IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'django_session') THEN
                    ALTER TABLE django_session ENABLE ROW LEVEL SECURITY;
                END IF;
                IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'django_migrations') THEN
                    ALTER TABLE django_migrations ENABLE ROW LEVEL SECURITY;
                END IF;
                IF EXISTS (SELECT FROM pg_tables WHERE schemaname = 'public' AND tablename = 'django_content_type') THEN
                    ALTER TABLE django_content_type ENABLE ROW LEVEL SECURITY;
                END IF;
            END $$;
        """)


def revert_custom_sql(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute("""
            DROP TRIGGER IF EXISTS trg_videos_updated ON videos;
            DROP TRIGGER IF EXISTS trg_settings_updated ON settings;
            DROP TRIGGER IF EXISTS trg_accounts_updated ON accounts;
            DROP FUNCTION IF EXISTS set_updated_at();
            DROP SEQUENCE IF EXISTS video_seq_seq;
        """)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(apply_custom_sql, revert_custom_sql),
    ]
