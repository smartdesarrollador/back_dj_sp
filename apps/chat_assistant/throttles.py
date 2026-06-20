from rest_framework.throttling import AnonRateThrottle


class ChatRateThrottle(AnonRateThrottle):
    scope = 'chat'
