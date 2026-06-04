from django.db import models
from django.utils import timezone
import hashlib
from decimal import Decimal


class AccessKey(models.Model):
    key_hash = models.CharField(max_length=128, unique=True)
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    @staticmethod
    def normalize(access_key: str) -> str:
        return ''.join([c for c in access_key.upper() if c.isalnum()])

    @staticmethod
    def validate_format(access_key: str) -> bool:
        k = AccessKey.normalize(access_key)
        return len(k) == 6 and k.isalnum()

    @staticmethod
    def hash_key(access_key: str) -> str:
        return hashlib.sha256(access_key.encode('utf-8')).hexdigest()

    @classmethod
    def store_key(cls, access_key: str, expires_at=None):
        normalized = cls.normalize(access_key)
        if not cls.validate_format(normalized):
            raise ValueError('Access key must contain 6 letters or numbers.')

        h = cls.hash_key(normalized)
        inst = cls.objects.create(key_hash=h, expires_at=expires_at)
        return {'id': inst.id, 'access_key': normalized, 'expires_at': expires_at}

    @classmethod
    def find_valid(cls, access_key: str):
        normalized = cls.normalize(access_key)
        if not cls.validate_format(normalized):
            return None

        h = cls.hash_key(normalized)
        try:
            row = cls.objects.get(key_hash=h, revoked_at__isnull=True)
        except cls.DoesNotExist:
            return None

        if row.expires_at is not None and row.expires_at <= timezone.now():
            return None

        row.last_used_at = timezone.now()
        row.save(update_fields=['last_used_at'])
        return {'id': row.id, 'expires_at': row.expires_at}


class PredictionSettings(models.Model):
    min_odds = models.DecimalField(max_digits=6, decimal_places=2, default=1.20)
    max_odds = models.DecimalField(max_digits=6, decimal_places=2, default=11.00)
    min_seconds_ahead = models.IntegerField(default=30)
    max_seconds_ahead = models.IntegerField(default=300)
    timezone = models.CharField(max_length=64, default='Africa/Dar_es_Salaam')

    class Meta:
        verbose_name = 'Prediction Settings'


class Prediction(models.Model):
    odds = models.DecimalField(max_digits=6, decimal_places=2)
    play_time = models.TimeField()
    created_at = models.DateTimeField(default=timezone.now)


class MonitorRoundOdds(models.Model):
    round_number = models.IntegerField(null=True, blank=True)
    payout = models.DecimalField(max_digits=12, decimal_places=2)
    raw_message = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = 'Monitor Round Odds'
        verbose_name_plural = 'Monitor Round Odds'

    def __str__(self):
        rn = f"Round #{self.round_number}" if self.round_number is not None else "Round"
        return f"{rn}: {self.payout} at {self.created_at.isoformat()}"


class MonitorState(models.Model):
    name = models.CharField(max_length=32, unique=True, default='default')
    state = models.JSONField(default=dict)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = 'Monitor State'
        verbose_name_plural = 'Monitor States'

    @classmethod
    def default_state(cls):
        return {
            'running': False,
            'started_at': None,
            'last_event': None,
            'event_count': 0,
            'awaiting_second_round_prediction': False,
            'last_round_over_at': None,
            'last_round_over_event': None,
            'last_prediction_phase': None,
            'last_prediction_at': None,
        }

    @classmethod
    def get_current(cls):
        obj, _ = cls.objects.get_or_create(name='default', defaults={'state': cls.default_state()})
        data = cls.default_state()
        data.update(obj.state or {})
        return obj, data

    @classmethod
    def save_current(cls, state):
        obj, _ = cls.objects.get_or_create(name='default', defaults={'state': cls.default_state()})
        obj.state = {**cls.default_state(), **(state or {})}
        obj.updated_at = timezone.now()
        obj.save(update_fields=['state', 'updated_at'])
        return obj


class MonitorLog(models.Model):
    message = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = 'Monitor Log'
        verbose_name_plural = 'Monitor Logs'

    def __str__(self):
        return f'{self.created_at.isoformat()} {self.message[:80]}'
