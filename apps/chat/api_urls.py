from rest_framework.routers import DefaultRouter
from apps.chat.views import ConversationViewSet

app_name = "chat-api"

router = DefaultRouter()
router.register(r"conversations", ConversationViewSet, basename="conversation")

urlpatterns = router.urls
