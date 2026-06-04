# Generated manually for Render/PostgreSQL deployment support.

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('api_app', '0002_monitorroundodds'),
    ]

    operations = [
        migrations.CreateModel(
            name='MonitorState',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(default='default', max_length=32, unique=True)),
                ('state', models.JSONField(default=dict)),
                ('updated_at', models.DateTimeField(default=django.utils.timezone.now)),
            ],
            options={
                'verbose_name': 'Monitor State',
                'verbose_name_plural': 'Monitor States',
            },
        ),
    ]