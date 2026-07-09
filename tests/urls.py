from django.urls import include, path

urlpatterns = [
    path("video/", include("stapel_video.urls")),
]
