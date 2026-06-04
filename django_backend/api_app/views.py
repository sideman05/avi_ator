import os
import json
import threading
import queue
import time
import sys
from datetime import datetime, timedelta
from django.conf import settings as django_settings
from django.http import JsonResponse, StreamingHttpResponse, HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from .models import AccessKey, PredictionSettings, Prediction, MonitorRoundOdds, MonitorState, MonitorLog
import re
import random

# Import prediction engine
try:
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    from monitor_round import AviatorPredictionEngine
except Exception:
    AviatorPredictionEngine = None

MONITOR_QUEUE = None
MONITOR_THREAD = None
MONITOR_STOP = None
ROUND_OVER_MESSAGE = '[OK] ROUND IS OVER - cashout dropped to 0'
ROUND_OVER_CORE_MESSAGE = 'ROUND IS OVER - cashout dropped to 0'
MONITOR_STATE = {
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


def _local_now():
    try:
        return timezone.localtime(timezone.now())
    except Exception:
        return datetime.now()


def _local_iso():
    return _local_now().isoformat()


def _format_local_datetime(value=None, include_millis=False):
    current = _local_now() if value is None else value
    try:
        current = timezone.localtime(current)
    except Exception:
        pass

    fmt = '%Y-%m-%d %H:%M:%S.%f' if include_millis else '%Y-%m-%d %H:%M:%S'
    formatted = current.strftime(fmt)
    return formatted[:-3] if include_millis else formatted


def _default_monitor_state():
    return MonitorState.default_state()


def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except Exception:
        return None
    if timezone.is_naive(parsed):
        try:
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        except Exception:
            pass
    return parsed


def _latest_round_over_log():
    try:
        return MonitorLog.objects.filter(message__contains=ROUND_OVER_CORE_MESSAGE).order_by('-created_at').first()
    except Exception:
        return None


def _apply_round_over_log_fallback(state):
    if state.get('last_round_over_at'):
        return state

    row = _latest_round_over_log()
    if row is None:
        return state

    state['last_round_over_at'] = row.created_at.isoformat()
    state['last_round_over_event'] = row.message

    last_prediction_at = _parse_iso_datetime(state.get('last_prediction_at'))
    if last_prediction_at is None or last_prediction_at < row.created_at:
        state['awaiting_second_round_prediction'] = True

    return state

def _load_monitor_state_from_db():
    try:
        obj, state = MonitorState.get_current()
        state['updated_at'] = obj.updated_at.isoformat()
        stale_after = int(os.environ.get('AVIATOR_MONITOR_STALE_SECONDS', '120'))
        if state.get('running') and obj.updated_at <= timezone.now() - timedelta(seconds=stale_after):
            state['running'] = False
        return _apply_round_over_log_fallback(state)
    except Exception:
        return {**_default_monitor_state(), **MONITOR_STATE}


def _persist_monitor_state():
    try:
        MonitorState.save_current(MONITOR_STATE)
    except Exception:
        pass


def _generate_key_value() -> str:
    alphabet = '23456789ABCDEFGHJKLMNPQRSTUVWXYZ'
    return ''.join(random.SystemRandom().choice(alphabet) for _ in range(6))


def access_keys_page(request):
    """Render the access keys generation page"""
    return render(request, 'access_keys.html')


def prediction_page(request):
    """Render a simple UI for generating a prediction."""
    return render(request, 'prediction.html', {
        'monitor_state': _load_monitor_state_from_db(),
        'round_over_message': ROUND_OVER_MESSAGE,
    })


@csrf_exempt
def generate_access_key(request):
    if request.method != 'POST':
        return JsonResponse({'error': 'Use POST'}, status=405)

    try:
        payload = json.loads(request.body.decode()) if request.body else request.POST.dict()
    except:
        payload = request.POST.dict()

    admin_token_required = os.environ.get('AVIATOR_ACCESS_KEY_ADMIN_TOKEN', '')
    if admin_token_required:
        submitted = payload.get('admin_token', '')
        if not submitted or submitted != admin_token_required:
            return JsonResponse({'error': 'Invalid admin token.'}, status=403)

    # Support both explicit valid_days and a simpler plan selector (week/month)
    plan = str(payload.get('plan', '') or '').strip().lower()
    valid_days_raw = payload.get('valid_days', None)

    if valid_days_raw not in (None, ''):
        try:
            valid_days = int(valid_days_raw)
        except Exception:
            return JsonResponse({'error': 'valid_days must be a number.'}, status=400)
    else:
        if plan == 'month':
            valid_days = 30
        elif plan == 'week':
            valid_days = 7
        else:
            valid_days = 7

    if valid_days <= 0:
        return JsonResponse({'error': 'valid_days must be greater than 0.'}, status=400)

    expires_at = None
    if valid_days > 0:
        expires_at = timezone.now() + timedelta(days=valid_days)

    value = _generate_key_value()
    stored = AccessKey.store_key(value, expires_at=expires_at)

    normalized_plan = 'month' if valid_days == 30 else 'week' if valid_days == 7 else 'custom'

    return JsonResponse({
        'access_key': stored['access_key'],
        'expires_at': stored['expires_at'],
        'valid_days': valid_days,
        'plan': normalized_plan,
    })


@csrf_exempt
def validate_access_key(request):
    if request.method == 'OPTIONS':
        return HttpResponse(status=204)

    if request.method != 'POST':
        return JsonResponse({'success': False, 'valid': False, 'message': 'Only POST supported'}, status=405)

    try:
        payload = json.loads(request.body.decode() or '{}')
    except Exception:
        payload = request.POST.dict()

    access_key = payload.get('access_key', '')
    row = AccessKey.find_valid(access_key)
    if row is None:
        return JsonResponse({'success': True, 'valid': False, 'message': 'Invalid or expired access key.'})

    return JsonResponse({'success': True, 'valid': True, 'data': {'expires_at': row['expires_at']}})


def _load_or_create_prediction_settings():
    settings = PredictionSettings.objects.first()
    if not settings:
        settings = PredictionSettings.objects.create()
    return settings


def prediction(request):
    if request.method == 'OPTIONS':
        return HttpResponse(status=204)
    if request.method != 'GET':
        return JsonResponse({'success': False, 'message': 'Only GET supported'}, status=405)

    settings = _load_or_create_prediction_settings()

    # compute
    min_odds = float(settings.min_odds)
    max_odds = float(settings.max_odds)
    if min_odds <= 0 or max_odds < min_odds:
        return JsonResponse({'success': False, 'message': 'Invalid odds settings.'}, status=500)

    gate_state = _get_prediction_gate_state()
    if not gate_state['ready']:
        return JsonResponse(
            {
                'success': False,
                'message': 'Wait for the monitor to report "ROUND IS OVER - cashout dropped to 0" before generating the second-round prediction.',
                'monitor_state': gate_state,
            },
            status=409,
        )

    # Theory-based prediction using the engine
    theory_prediction = None
    engine_signals = []
    confidence_level = 'NONE'
    prediction_score = 0
    market_state = 'unknown'
    should_alert = False

    if AviatorPredictionEngine:
        try:
            # Load historical odds from database
            odds_rows = MonitorRoundOdds.objects.order_by('created_at').values_list('payout', flat=True)[:100]
            odds_history = [float(o) for o in odds_rows]
            
            if odds_history:
                engine = AviatorPredictionEngine()
                for odd in odds_history:
                    engine.add_round(odd)
                
                theory_prediction = engine.build_prediction()
                engine_signals = theory_prediction.get('signals', [])
                confidence_level = theory_prediction.get('confidence_level', 'NONE')
                prediction_score = theory_prediction.get('score', 0)
                market_state = theory_prediction.get('market', {}).get('state', 'unknown')
                should_alert = theory_prediction.get('should_alert', False)
        except Exception as e:
            # Fallback to random if engine fails
            pass

    # Generate odds: use theory confidence to bias selection
    if should_alert and confidence_level in ('HIGH', 'MEDIUM'):
        # Theory says watch for pink - bias toward higher odds
        r = random.random()
        weighted = r ** 1.15  # Steeper curve = more high odds
    else:
        # Normal distribution
        r = random.random()
        weighted = r ** 1.35
    
    odds = round(min_odds + ((max_odds - min_odds) * weighted), 2)

    current_time = _local_now()
    seconds_ahead = random.randint(settings.min_seconds_ahead, settings.max_seconds_ahead)
    play_time = current_time + timedelta(seconds=seconds_ahead)

    pred = Prediction.objects.create(odds=odds, play_time=play_time.time())

    _set_monitor_state(
        awaiting_second_round_prediction=False,
        last_prediction_phase='second_round',
        last_prediction_at=current_time.isoformat(),
        event_count=MONITOR_STATE['event_count'],
    )

    payload = {
        'id': pred.id,
        'odds': float(pred.odds),
        'play_time': play_time.strftime('%H:%M:%S'),
        'current_time': current_time.strftime('%H:%M:%S'),
        'current_time_iso': current_time.isoformat(),
        'current_timestamp': int(current_time.timestamp()),
        'next_play_time': play_time.strftime('%H:%M:%S'),
        'next_play_at': play_time.strftime('%Y-%m-%d %H:%M:%S'),
        'next_play_at_iso': play_time.isoformat(),
        'next_play_timestamp': int(play_time.timestamp()),
        'seconds_until_play': seconds_ahead,
        'generated_at': current_time.strftime('%Y-%m-%d %H:%M:%S'),
        'generated_at_iso': current_time.isoformat(),
        'generated_timestamp': int(current_time.timestamp()),
        'timezone': settings.timezone,
        'round_phase': 'second_round',
        'round_number': 2,
        'monitor_triggered': True,
        'monitor_trigger_event': gate_state['last_round_over_event'] or gate_state['last_event'],
        'monitor_triggered_at': gate_state['last_round_over_at'],
        'theory_prediction': {
            'engine_available': AviatorPredictionEngine is not None,
            'confidence_level': confidence_level,
            'prediction_score': prediction_score,
            'market_state': market_state,
            'should_alert': should_alert,
            'active_signals': engine_signals,
            'odds_influenced_by_theory': should_alert and confidence_level in ('HIGH', 'MEDIUM'),
        }
    }

    return JsonResponse({'success': True, 'data': payload})


def prediction_proxy(request):
    import requests

    UPSTREAM = os.environ.get('PREDICTION_UPSTREAM_URL', 'https://www.betpawa.co.tz/casino/game/aviator')
    if request.method == 'OPTIONS':
        return HttpResponse(status=204)
    if request.method != 'GET':
        return JsonResponse({'success': False, 'message': 'Only GET supported'}, status=405)

    try:
        resp = requests.get(UPSTREAM, timeout=15, headers={'Accept': 'application/json', 'User-Agent': 'AviatorPredictionProxy/1.0'})
        content_type = resp.headers.get('Content-Type', '')
        if 'application/json' in content_type:
            body = resp.json()
            return JsonResponse({'success': resp.status_code >= 200 and resp.status_code < 300, 'source': 'proxy', 'upstream_status': resp.status_code, 'content_type': content_type, 'data': body})
        else:
            return JsonResponse({'success': False, 'source': 'proxy', 'upstream_status': resp.status_code, 'content_type': content_type, 'message': 'Upstream returned non-JSON', 'data_preview': resp.text[:1200]})
    except Exception as e:
        return JsonResponse({'success': False, 'message': 'Failed to fetch upstream', 'error': str(e)}, status=502)


def monitor_page(request):
    shared_state = _load_monitor_state_from_db()
    return render(request, 'monitor.html', {
        'is_running': bool(shared_state.get('running')),
        'monitor_state': shared_state,
    })


def _set_monitor_state(
    *,
    running=None,
    started_at=None,
    last_event=None,
    event_count=None,
    last_prediction_phase=None,
    last_prediction_at=None,
    last_round_over_at=None,
    last_round_over_event=None,
    awaiting_second_round_prediction=None,
):
    try:
        _, current_state = MonitorState.get_current()
        MONITOR_STATE.update(current_state or {})
    except Exception:
        pass

    if running is not None:
        MONITOR_STATE['running'] = running
    if started_at is not None:
        MONITOR_STATE['started_at'] = started_at
    if last_event is not None:
        MONITOR_STATE['last_event'] = last_event
    if event_count is not None:
        MONITOR_STATE['event_count'] = event_count
    if last_prediction_phase is not None:
        MONITOR_STATE['last_prediction_phase'] = last_prediction_phase
    if last_prediction_at is not None:
        MONITOR_STATE['last_prediction_at'] = last_prediction_at
    if last_round_over_at is not None:
        MONITOR_STATE['last_round_over_at'] = last_round_over_at
    if last_round_over_event is not None:
        MONITOR_STATE['last_round_over_event'] = last_round_over_event
    if awaiting_second_round_prediction is not None:
        MONITOR_STATE['awaiting_second_round_prediction'] = awaiting_second_round_prediction

    _persist_monitor_state()


def _record_monitor_event(message: str):
    """Track monitor log messages and detect the round-over trigger."""
    MONITOR_STATE['last_event'] = message

    # Try to parse an optional leading timestamp like: [YYYY-MM-DD HH:MM:SS.mmm]
    text = message
    parsed_timestamp = None
    if text.startswith('['):
        try:
            end_idx = text.index(']')
            ts_str = text[1:end_idx]
            try:
                parsed_ts = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S.%f')
                try:
                    # make timezone-aware using project timezone
                    parsed_timestamp = timezone.make_aware(parsed_ts, timezone.get_current_timezone())
                except Exception:
                    parsed_timestamp = parsed_ts
                text = text[end_idx + 1 :].strip()
            except Exception:
                parsed_timestamp = None
        except Exception:
            parsed_timestamp = None

    # Detect round over
    if ROUND_OVER_MESSAGE in text or ROUND_OVER_CORE_MESSAGE in text:
        MONITOR_STATE['awaiting_second_round_prediction'] = True
        # prefer parsed timestamp if present
        MONITOR_STATE['last_round_over_at'] = (parsed_timestamp.isoformat() if parsed_timestamp is not None else _local_iso())
        MONITOR_STATE['last_round_over_event'] = message

    # Parse explicit round payout/odds lines like: "Round #32: payout/odds = 13.00x"
    try:
        m = re.search(r"Round\s+#(\d+):\s*payout/odds\s*=\s*([0-9]+(?:\.[0-9]+)?)x", text)
        if m:
            rn = int(m.group(1))
            payout_val = float(m.group(2))
            # store as decimal with two places and use parsed timestamp when available
            create_kwargs = {
                'round_number': rn,
                'payout': round(payout_val, 2),
                'raw_message': text,
            }
            if parsed_timestamp is not None:
                create_kwargs['created_at'] = parsed_timestamp

            MonitorRoundOdds.objects.create(**create_kwargs)
    except Exception:
        pass

    _persist_monitor_state()


def _get_prediction_gate_state():
    """Return the current monitor gate state for second-round prediction generation."""
    shared_state = _load_monitor_state_from_db()
    awaiting = bool(shared_state.get('awaiting_second_round_prediction'))
    last_event = shared_state.get('last_event')
    last_round_over_at = shared_state.get('last_round_over_at')
    last_round_over_event = shared_state.get('last_round_over_event')
    last_prediction_at = shared_state.get('last_prediction_at')

    if not last_round_over_at:
        row = _latest_round_over_log()
        if row is not None:
            last_round_over_at = row.created_at.isoformat()
            last_round_over_event = row.message

    round_over_pending = False
    if awaiting:
        round_over_pending = True
    elif last_round_over_at:
        if not last_prediction_at:
            round_over_pending = True
        else:
            try:
                parsed_prediction_at = _parse_iso_datetime(last_prediction_at)
                parsed_round_over_at = _parse_iso_datetime(last_round_over_at)
                round_over_pending = (
                    parsed_prediction_at is None
                    or parsed_round_over_at is None
                    or parsed_prediction_at < parsed_round_over_at
                )
            except Exception:
                round_over_pending = True

    return {
        'ready': round_over_pending,
        'awaiting_second_round_prediction': awaiting,
        'last_event': last_event,
        'last_round_over_at': last_round_over_at,
        'last_round_over_event': last_round_over_event,
        'last_prediction_at': last_prediction_at,
    }


def _monitor_runner(q: queue.Queue, stop_event: threading.Event):
    """Background runner that attempts to use monitor_round.AviatorRoundMonitor if available."""
    def push(message: str):
        timestamped = f"[{_format_local_datetime(include_millis=True)}] {message}"
        _set_monitor_state(last_event=timestamped, event_count=MONITOR_STATE['event_count'] + 1)
        _record_monitor_event(message)
        try:
            MonitorLog.objects.create(message=timestamped)
        except Exception:
            pass
        try:
            q.put_nowait(timestamped)
        except queue.Full:
            try:
                q.get_nowait()
            except Exception:
                pass
            try:
                q.put_nowait(timestamped)
            except Exception:
                pass

    try:
        import sys
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)

        import monitor_round
        MonitorClass = getattr(monitor_round, 'AviatorRoundMonitor', None)
    except Exception:
        MonitorClass = None

    if MonitorClass is None:
        push('[WARN] monitor_round.py could not be imported; using simulated output.')
        while not stop_event.is_set():
            push(f'SIMULATED: cashout {round(random.random() * 10, 2)}')
            time.sleep(1)
        push('[INFO] Monitor stopped (simulated).')
        return

    try:
        phone = os.environ.get('AVIATOR_PHONE', '0618587348')
        password = os.environ.get('AVIATOR_PASSWORD', '5002')
        login_button_selector = os.environ.get(
            'AVIATOR_LOGIN_BUTTON_SELECTOR',
            'button[data-test-id="logInButton"],button._button_1h7bd_1._primary_1h7bd_62._lg_1h7bd_47._square_1h7bd_54._fullWidth_1h7bd_144',
        )

        monitor = MonitorClass(
            headless=True,
            browser=os.environ.get('AVIATOR_BROWSER', 'auto'),
            phone=phone,
            password=password,
            login_button_selectors=login_button_selector,
            check_interval=float(os.environ.get('AVIATOR_CHECK_INTERVAL', '0.5')),
            wait_timeout=int(os.environ.get('AVIATOR_WAIT_TIMEOUT', '45')),
            max_iframe_depth=int(os.environ.get('AVIATOR_MAX_IFRAME_DEPTH', '6')),
        )

        monitor.log_event = push
        MONITOR_STATE['awaiting_second_round_prediction'] = False
        MONITOR_STATE['last_round_over_at'] = None
        MONITOR_STATE['last_round_over_event'] = None
        MONITOR_STATE['last_prediction_phase'] = None
        MONITOR_STATE['last_prediction_at'] = None
        _set_monitor_state(running=True, started_at=_local_iso(), last_event='Monitor worker started', event_count=0)

        def run_monitor():
            try:
                monitor.start_monitoring(duration=None)
            except Exception as exc:
                push(f'[ERROR] Monitor failed: {exc}')

        worker = threading.Thread(target=run_monitor, daemon=True)
        worker.start()

        while not stop_event.is_set():
            if not worker.is_alive():
                break
            time.sleep(0.25)

        try:
            monitor.stop()
        except Exception:
            pass

        push('[INFO] Monitor stopped.')
    except Exception as exc:
        push(f'[ERROR] Monitor runner crashed: {exc}')
    finally:
        _set_monitor_state(running=False)


def monitor_start(request):
    global MONITOR_QUEUE, MONITOR_THREAD, MONITOR_STOP
    if not django_settings.DEBUG:
        return JsonResponse({
            'success': True,
            'message': 'Monitor is managed by the Render worker service.',
            'state': _load_monitor_state_from_db(),
        })

    if MONITOR_THREAD and MONITOR_THREAD.is_alive():
        return JsonResponse({'success': True, 'message': 'Monitor already running', 'state': MONITOR_STATE})

    try:
        MonitorLog.objects.all().delete()
    except Exception:
        pass

    MONITOR_QUEUE = queue.Queue(maxsize=1000)
    MONITOR_STOP = threading.Event()
    MONITOR_THREAD = threading.Thread(target=_monitor_runner, args=(MONITOR_QUEUE, MONITOR_STOP), daemon=True)
    MONITOR_THREAD.start()
    MONITOR_STATE['awaiting_second_round_prediction'] = False
    MONITOR_STATE['last_round_over_at'] = None
    MONITOR_STATE['last_round_over_event'] = None
    MONITOR_STATE['last_prediction_phase'] = None
    MONITOR_STATE['last_prediction_at'] = None
    _set_monitor_state(running=True, started_at=_local_iso(), last_event='Monitor start requested', event_count=0)
    return JsonResponse({'success': True, 'message': 'Monitor started', 'state': MONITOR_STATE})


def monitor_stop(request):
    global MONITOR_QUEUE, MONITOR_THREAD, MONITOR_STOP
    if not django_settings.DEBUG:
        return JsonResponse({
            'success': True,
            'message': 'Monitor stop is managed by the Render worker service.',
            'state': _load_monitor_state_from_db(),
        })

    if MONITOR_STOP:
        MONITOR_STOP.set()
    _set_monitor_state(running=False, last_event='Stop requested')
    MONITOR_STATE['awaiting_second_round_prediction'] = False
    _persist_monitor_state()
    return JsonResponse({'success': True, 'message': 'Stop requested', 'state': MONITOR_STATE})


def monitor_status(request):
    global MONITOR_THREAD, MONITOR_STOP
    shared_state = _load_monitor_state_from_db()
    return JsonResponse({
        'success': True,
        'running': bool(shared_state.get('running')),
        'state': shared_state,
    })


def monitor_odds(request):
    if request.method == 'OPTIONS':
        return HttpResponse(status=204)
    if request.method != 'GET':
        return JsonResponse({'success': False, 'message': 'Only GET supported'}, status=405)

    qs = MonitorRoundOdds.objects.order_by('-created_at')[:200]
    out = []
    for row in qs:
        try:
            ts = _format_local_datetime(row.created_at, include_millis=True)
        except Exception:
            ts = row.created_at.isoformat()
        line = f'[{ts}] Round #{row.round_number}: payout/odds = {float(row.payout):.2f}x'
        out.append({'id': row.id, 'round': row.round_number, 'payout': float(row.payout), 'created_at': row.created_at.isoformat(), 'line': line, 'raw_message': row.raw_message})

    return JsonResponse({'success': True, 'data': out})


def monitor_stream(request):
    global MONITOR_STOP
    def event_stream():
        heartbeat_at = time.monotonic()
        last_log_id = 0
        if not django_settings.DEBUG:
            try:
                last_log_id = MonitorLog.objects.order_by('-id').values_list('id', flat=True).first() or 0
            except Exception:
                last_log_id = 0

        while True:
            if MONITOR_STOP and MONITOR_STOP.is_set():
                yield 'event: status\ndata: stopped\n\n'
                break

            try:
                rows = MonitorLog.objects.filter(id__gt=last_log_id).order_by('id')[:100]
                emitted = False
                for row in rows:
                    last_log_id = row.id
                    emitted = True
                    yield f'event: log\ndata: {row.message}\n\n'
                if emitted:
                    heartbeat_at = time.monotonic()
                    continue
            except Exception:
                pass

            now = time.monotonic()
            if now - heartbeat_at >= 15:
                heartbeat_at = now
                yield f'event: heartbeat\ndata: {_local_iso()}\n\n'
                continue

            time.sleep(1)

    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    response['X-Accel-Buffering'] = 'no'
    return response
