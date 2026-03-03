from channels.generic.websocket import AsyncWebsocketConsumer


class ControlChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.uuid = self.scope["url_route"]["kwargs"]["uuid"]
        self.group_name = f"control_chat_{self.uuid}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def control_chat_message(self, event):
        await self.send_json(event["message"])
