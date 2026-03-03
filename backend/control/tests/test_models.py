import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError

from control.models import (
    Role,
    Operator,
    ControlConversation,
    ControlMessage,
    IngestEvent,
)


User = get_user_model()


@pytest.mark.django_db
def test_operator_and_role_creation():
    role = Role.objects.create(key="operator", name="Operator")
    user = User.objects.create_user(username="op", password="secret")
    operator = Operator.objects.create(user=user)
    operator.roles.add(role)
    assert operator.roles.count() == 1
    assert role.operators.first() == operator


@pytest.mark.django_db
def test_control_conversation_and_message():
    convo = ControlConversation.objects.create(kind="comms_mirror", title="test")
    msg = ControlMessage.objects.create(
        conversation=convo,
        direction="in",
        author_type="transport_user",
        author_label="tele-user",
        text="hello",
    )
    assert convo.messages.first() == msg
    assert msg.text == "hello"


@pytest.mark.django_db
def test_ingest_event_unique():
    IngestEvent.objects.create(transport="telegram", external_event_id="42")
    with pytest.raises(IntegrityError):
        IngestEvent.objects.create(transport="telegram", external_event_id="42")
