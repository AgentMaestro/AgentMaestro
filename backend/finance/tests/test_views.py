from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse

import pytest


User = get_user_model()


@pytest.mark.django_db
def test_research_home_requires_login():
    client = Client()
    response = client.get(reverse("finance:research_home"))
    assert response.status_code == 302


@pytest.mark.django_db
def test_research_home_renders_compact_research_shell():
    user = User.objects.create_user(username="finance-user", password="secret")
    client = Client()
    client.force_login(user)

    response = client.get(reverse("finance:research_home"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Portfolio and research workspace" in content
    assert "AI Research Chat" in content
    assert "Moving Averages" in content
    assert "ATR" in content
    assert "Black-Scholes" in content
