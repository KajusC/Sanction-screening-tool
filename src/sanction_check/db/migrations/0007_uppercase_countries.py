from django.apps.registry import Apps
from django.db import migrations
from django.db.backends.base.schema import BaseDatabaseSchemaEditor

BATCH_SIZE = 5000


def uppercase_stored_countries(apps: Apps, _schema_editor: BaseDatabaseSchemaEditor) -> None:
    entity_model = apps.get_model("db", "Entity")
    entities = list(entity_model.objects.filter(countries__len__gt=0).only("id", "countries"))
    for entity in entities:
        entity.countries = [country.upper() for country in entity.countries]
    entity_model.objects.bulk_update(entities, ["countries"], batch_size=BATCH_SIZE)


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0006_entity_source_datasets"),
    ]

    operations = [
        migrations.RunPython(uppercase_stored_countries, migrations.RunPython.noop),
    ]
