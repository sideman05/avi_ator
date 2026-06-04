from django.urls import path
from . import views

urlpatterns = [
    path('access-keys/', views.access_keys_page, name='access_keys_page'),
    path('prediction/', views.prediction_page, name='prediction_page'),
    path('api/access-keys/generate/', views.generate_access_key, name='generate_access_key'),
    path('api/access-keys/validate/', views.validate_access_key, name='validate_access_key'),
    path('api/prediction/', views.prediction, name='prediction'),
    path('api/prediction-proxy/', views.prediction_proxy, name='prediction_proxy'),
    path('monitor/', views.monitor_page, name='monitor_page'),
    path('monitor/stream/', views.monitor_stream, name='monitor_stream'),
    path('monitor/status/', views.monitor_status, name='monitor_status'),
    path('monitor/odds/', views.monitor_odds, name='monitor_odds'),
    path('monitor/start/', views.monitor_start, name='monitor_start'),
    path('monitor/stop/', views.monitor_stop, name='monitor_stop'),
]
