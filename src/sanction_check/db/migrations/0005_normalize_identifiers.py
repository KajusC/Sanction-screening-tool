from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

from sanction_check.db.models import BULK_BATCH_SIZE
from sanction_check.matching.normalize import normalize_identifiers


def normalize_stored_identifiers(
    apps: Apps, _schema_editor: BaseDatabaseSchemaEditor
) -> None:
    entity_model = apps.get_model("db", "Entity")
    entities = list(
        entity_model.objects.filter(identifiers__len__gt=0).only("id", "identifiers")
    )
    for entity in entities:
        entity.identifiers = normalize_identifiers(entity.identifiers)
    entity_model.objects.bulk_update(
        entities, ["identifiers"], batch_size=BULK_BATCH_SIZE
    )


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0004_entityname_embedding_and_more"),
    ]

    operations = [
        migrations.RunPython(normalize_stored_identifiers, migrations.RunPython.noop),
    ]
