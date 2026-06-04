# Generated manually for Render/PostgreSQL deployment support.

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('api_app', '0003_monitorstate'),
    ]

    operations = [
        migrations.CreateModel(
            name='MonitorLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('message', models.TextField()),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'verbose_name': 'Monitor Log',
                'verbose_name_plural': 'Monitor Logs',
            },
        ),
    ]